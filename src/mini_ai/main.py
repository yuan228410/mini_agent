import json
import argparse
import threading
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

from . import __version__
from .cli import CommandHandler, Display
from .memory import MemoryStore, Compactor, HistoryDB, HistoryDBPool
from .config import DATA_DIR, PACKAGE_DIR, SKILL_PATHS, _raw, user_data_dir
from .llm import get_usage, reset_usage, estimate_tokens, chat as llm_chat
from .llm.base import rebuild_tool_messages
from .context import ContextBuilder
from .core import ChatSession, HistoryPersister, ApplicationService, RunTurnOptions, build_session_runtime, build_settings_snapshot
from .core.runtime_context import SessionIdentity, SessionRuntimeContext
from .core.settings import SettingsSnapshot
from .logger import logger
from .runner import run_tool_loop
from .skills import SkillLoader
from .subagents import SubagentLoader
from .team import MessageBus, TeammateManager, Blackboard
from .team.loop import wait_for_teammates, shutdown_teammates, cleanup_inbox
from .tools import inject_todos as _inject_todos
from .plan.artifact_parser import strip_artifact_blocks
from .plan.prompts import build_plan_user_message
from .plan.schema import PlanArtifact
from .plan.service import PlanService
from .plan.store import PlanStore
from .plan.tool_policy import ToolPolicy, filter_tools
from .workspace import WorkspaceManager


# ═══════════════════════════════════════════
# 应用全局上下文 — 跨 workspace 重载的共享状态
# ═══════════════════════════════════════════

@dataclass
class AppContext:
    """跨 workspace 重载的全局可变状态。"""
    bus: MessageBus | None = None
    mcp_loader: object | None = None
    lead_tools_cache: list[dict] | None = None
    settings: SettingsSnapshot | None = None


_app_ctx = AppContext()
_subagent_loader = SubagentLoader(PACKAGE_DIR / "subagents")


def get_app_context() -> AppContext:
    """供外部模块获取全局 AppContext（如 commands.py 的 /mcp 命令）。"""
    return _app_ctx


# ═══════════════════════════════════════════
# MCP 初始化 / 关闭
# ═══════════════════════════════════════════

def _init_mcp(ctx: AppContext):
    settings = ctx.settings.mcp if ctx.settings else None
    if not settings or not settings.enabled or not settings.servers:
        return
    try:
        from .tools.mcp_loader import MCPLoader
    except ImportError:
        logger.warning("[MCP] mcp 包未安装，跳过 MCP 初始化 (pip install mcp)")
        return
    ctx.mcp_loader = MCPLoader(settings)
    modules = ctx.mcp_loader.start_sync()
    if modules:
        ctx.lead_tools_cache = None
        logger.info(f"[MCP] 已加载 {len(modules)} 个 MCP 工具")


def _shutdown_mcp(ctx: AppContext):
    if ctx.mcp_loader:
        ctx.mcp_loader.stop_sync()
        ctx.mcp_loader = None


# ═══════════════════════════════════════════
# 工具辅助
# ═══════════════════════════════════════════

def _lead_tool_defs(ctx: AppContext, registry) -> list[dict]:
    # 工具定义来自当前 session-local registry；不再读取模块级全局 registry。
    return [
        d for d in registry.get_definitions()
        if d["function"]["name"] not in ("read_inbox", "list_teammates")
    ]



# ═══════════════════════════════════════════
# Workspace 会话
# ═══════════════════════════════════════════

@dataclass
class SessionContext:
    """一个 workspace 对应的完整会话状态。"""
    username: str
    session_id: str
    skill_loader: SkillLoader
    store: MemoryStore
    history_db: HistoryDB
    compactor: Compactor
    context_builder: ContextBuilder
    team_mgr: TeammateManager
    blackboard: Blackboard
    lead_event: threading.Event
    runtime: SessionRuntimeContext | None = None
    cmd: CommandHandler = None  # 主循环 handler，reload 解包时赋予

    @property
    def tool_registry(self):
        return self.runtime.tool_registry if self.runtime else None


def _create_workspace_session(
    ws,
    disp: Display,
    app_ctx: AppContext,
    ws_mgr: WorkspaceManager,
    username: str = "default",
    session_id: str | None = None,
    force_new_session: bool = False,
) -> tuple[SessionContext, list[dict]]:
    """为指定 workspace 创建完整的会话状态，返回 (session, messages, cmd_handler)。
    
    Args:
        ws: 工作空间对象
        disp: 显示器
        app_ctx: 应用上下文
        ws_mgr: 工作空间管理器
        username: 用户名
        session_id: 指定的会话ID（None 表示自动选择）
        force_new_session: 是否强制创建新会话（仅当 session_id=None 时有效）
    """
    import uuid
    ws_dir = ws.ws_dir
    ws_name = ws.name

    logger.info(f"[Workspace] {ws_name} → {ws_dir} (project: {ws.project_path or Path.cwd()})")

    project_path = ws.project_path or str(Path.cwd())

    # ── 技能 ──
    user_skills_dir = user_data_dir(username) / "skills"
    ws_skills_dir = ws_dir / "skills"
    skill_loader = SkillLoader(DATA_DIR / "skills", SKILL_PATHS,
                               user_skills_dir=user_skills_dir,
                               workspace_skills_dir=ws_skills_dir)

    app_ctx.settings = app_ctx.settings or build_settings_snapshot()

    # ── Team + Bus ──
    bus = MessageBus(ws_dir / ".team" / "inbox")
    app_ctx.bus = bus
    team_mgr = TeammateManager(team_dir=ws_dir / ".team", bus=bus, project_dir=ws_dir)

    # ── MCP ──
    _init_mcp(app_ctx)

    # ── Blackboard + Workflow ──
    bb = Blackboard(persist_path=ws_dir / ".team" / "blackboard.json")

    lead_event = threading.Event()
    bus.register_wake("lead", lead_event)

    # ── 记忆 + 历史 ──
    global_memory_dir = DATA_DIR / "memory"
    user_memory_dir = user_data_dir(username) / "memory"
    ws_memory_dir = ws_dir / "memory_data"
    
    # 先创建 history_db（用于检查会话是否存在）
    history_db = HistoryDBPool.get(username)  # 使用指定用户
    
    # 会话 ID 决策逻辑
    if session_id:
        # 指定了会话ID，检查是否存在
        existing = history_db.load_session(ws_name, session_id, limit=1)
        if existing:
            logger.info(f"[会话] 恢复会话: {session_id}")
            disp.info(f"恢复会话: {session_id}")
        else:
            # 指定的会话不存在，报错退出
            logger.error(f"[会话] 会话 {session_id} 不存在")
            disp.error(f"错误：会话 {session_id} 不存在")
            raise ValueError(f"会话 {session_id} 不存在")
    elif force_new_session:
        # 强制创建新会话
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        logger.info(f"[会话] 创建新会话: {session_id}")
        disp.info(f"创建新会话: {session_id}")
    else:
        # 未指定会话ID，尝试使用最新会话
        latest_sid = history_db.get_latest_session(ws_name)
        if latest_sid:
            session_id = latest_sid
            logger.info(f"[会话] 恢复最新会话: {session_id}")
            disp.info(f"恢复最新会话: {session_id}")
        else:
            # 没有任何会话，创建新的
            session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
            logger.info(f"[会话] 创建新会话: {session_id}")
            disp.info(f"创建新会话: {session_id}")
    
    # 会话级记忆目录（与 Web 端一致）
    sessions_base = ws_dir / "sessions"
    sessions_base.mkdir(parents=True, exist_ok=True)
    session_dir = sessions_base / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session_memory_dir = session_dir / "memory_data"
    session_memory_dir.mkdir(parents=True, exist_ok=True)
    
    # MemoryStore：与 Web 端一致，第一参数为用户级记忆
    store = MemoryStore(
        user_memory_dir,
        episode_dir=session_memory_dir,
        global_memory_dir=global_memory_dir,
        workspace_memory_dir=ws_memory_dir,
    )

    identity = SessionIdentity(username=username, workspace=ws_name, session_id=session_id, project_path=project_path)

    # ── Context ──
    ctx_builder = ContextBuilder(DATA_DIR)

    # ── Compactor ──
    compactor_settings = app_ctx.settings.compactor
    compactor = Compactor(
        store,
        keep_recent=compactor_settings.keep_recent,
        context_usage_threshold=compactor_settings.context_usage_threshold,
        keep_budget_ratio=compactor_settings.keep_budget_ratio,
        early_compact_ratio=compactor_settings.early_compact_ratio,
        max_cached_summaries=compactor_settings.max_cached_summaries,
        max_summary_sections=compactor_settings.max_summary_sections,
        context_length=app_ctx.settings.model.context_length,
        context_builder=ctx_builder,
        skill_loader=skill_loader,
        project_path=ws.project_path or str(Path.cwd()),
        summary_dir=session_dir,
    )

    # ── Runtime + CommandHandler（含 run_loop 闭包）──
    runtime = build_session_runtime(
        identity=identity,
        messages=[],
        display=disp,
        history_db=history_db,
        memory_store=store,
        skill_loader=skill_loader,
        subagent_loader=_subagent_loader,
        bus=bus,
        team_mgr=team_mgr,
        blackboard=bb,
        mcp_loader=app_ctx.mcp_loader,
        compactor=compactor,
        context_builder=ctx_builder,
        settings=app_ctx.settings,
    )
    tool_registry = runtime.tool_registry
    req_ctx = runtime.request_context
    settings = runtime.settings

    def _run_loop(messages, tools, inject_fn, disp, ctx=None):
        msg, _ = run_tool_loop(
            messages, tools,
            streaming=settings.streaming,
            display=disp,
            inject_fn=inject_fn,
            ctx=ctx,
            bus=app_ctx.bus,
            context_length=settings.model.context_length,
            max_turns=settings.runner.max_turns,
            context_usage_limit=settings.runner.context_usage_limit,
            compactor=compactor,
            tool_registry=tool_registry,
            timeout_settings=settings.timeouts,
            max_consecutive_errors=int(settings.runner.extra.get("max_consecutive_errors", 3)),
        )
        return msg

    disp.todo_session_id = session_id
    cmd = CommandHandler(
        disp=disp, store=store, compactor=compactor,
        inject_fn=_inject_todos, run_tool_fn=_run_loop,
        lead_tools=_lead_tool_defs(app_ctx, tool_registry), ctx=req_ctx, workspace_mgr=ws_mgr,
        history_db=history_db, context_builder=ctx_builder, skill_loader=skill_loader,
        project_path=ws.project_path, username=username, session_id=session_id,
        tool_registry=tool_registry,
    )

    # ── 构建消息 ──
    system_prompt = ctx_builder.build(memory_store=store, skill_loader=skill_loader,
                                       project_path=ws.project_path)
    messages = [{"role": "system", "content": system_prompt}]
    runtime.messages = messages
    _inject_todos(messages)

    # 加载指定会话的历史消息
    ctx_limit = app_ctx.settings.compactor.context_limit
    restored = history_db.load_session(ws_name, session_id, limit=ctx_limit)
    if restored:
        cleaned = rebuild_tool_messages(restored)
        messages.extend(cleaned)
        logger.info(f"[恢复] 会话 {session_id} 的 {len(restored)} 条历史消息（清理后 {len(cleaned)} 条）")

    session = SessionContext(
        username=username,
        session_id=session_id,
        skill_loader=skill_loader,
        store=store,
        history_db=history_db,
        compactor=compactor,
        context_builder=ctx_builder,
        team_mgr=team_mgr,
        blackboard=bb,
        lead_event=lead_event,
        runtime=runtime,
        cmd=cmd,
    )
    return session, messages


# ═══════════════════════════════════════════
# main
# ═══════════════════════════════════════════

def _flush_deferred(history_db, messages, deferred_list, workspace: str, session_id: str):
    """把延迟的 assistant 消息（含 tool_calls）写入历史 DB。"""
    tool_results_map = {m.get("tool_call_id", ""): m.get("content", "")
                        for m in messages if m.get("role") == "tool"}
    for am in deferred_list:
        enriched_tcs = []
        for tc in am.get("tool_calls", []):
            tc_copy = dict(tc)
            tc_id = tc.get("id", "")
            if tc_id and tc_id in tool_results_map:
                tc_copy["_result"] = tool_results_map[tc_id][:2000]
            enriched_tcs.append(tc_copy)
        am_meta = {}
        if am.get("thinking"):
            am_meta["thinking"] = am["thinking"]
        am_meta["tool_calls"] = enriched_tcs
        history_db.append(workspace, session_id, "assistant", am.get("content") or "",
                          metadata=json.dumps(am_meta))
    deferred_list.clear()


def main():
    # 配置已在模块导入时自动初始化并加载（config.py init_config）
    
    parser = argparse.ArgumentParser(prog="mini-ai", description="智能对话 Agent")
    parser.add_argument("-v", "--version", action="version",
                        version=f"mini-ai {__version__}")
    parser.add_argument("--web", action="store_true", help="启动 Web 界面")
    parser.add_argument("--port", type=int, default=8765, help="Web 端口 (默认 8765)")
    parser.add_argument("--user", type=str, default="default", help="用户名 (默认 default)")
    parser.add_argument("--workspace", type=str, help="工作空间名称 (默认当前目录名)")
    parser.add_argument("--session", type=str, help="会话 ID，不指定则自动生成新会话")
    parser.add_argument("--new-session", action="store_true", help="强制创建新会话")
    args = parser.parse_args()

    # 启动配置热加载
    from .config import start_config_watcher
    start_config_watcher()
    import atexit
    from .config import stop_config_watcher
    atexit.register(stop_config_watcher)

    # ── Web 模式 ──
    if args.web:
        from .web.app import create_app
        import uvicorn
        dist_dir = Path(__file__).parent.parent.parent / "web" / "dist"
        if not dist_dir.exists():
            print("提示: 前端未构建，请先执行: cd web && pnpm install && pnpm build")
            print("      开发模式可分别启动后端和前端 (pnpm dev)")
        print(f"mini_ai Web 界面启动: http://localhost:{args.port}")
        import os, sys, threading

        app = create_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=args.port,
                                timeout_graceful_shutdown=3)
        server = uvicorn.Server(config)

        try:
            server.run()
        except KeyboardInterrupt:
            pass

        from .memory.history_db import HistoryDBPool
        HistoryDBPool.close_all()
        os._exit(0)

    # ── CLI 模式 ──
    # 使用用户数据目录（与 Web 模式一致）
    from .config import user_data_dir
    user_root = user_data_dir(args.user)
    ws_mgr = WorkspaceManager(user_root, ensure_default=False)

    settings = build_settings_snapshot()

    # 显示
    disp = Display(
        thinking_mode=settings.display.thinking_mode,
        tool_detail=settings.display.tool_detail,
        on_status_update=None,  # 延后设置
    )

    # 确定工作空间名称
    if args.workspace:
        # 用户指定了工作空间名称
        ws_name = args.workspace
        ws = ws_mgr.get(ws_name)
        if not ws:
            # 不存在则自动创建
            ws_mgr.create(ws_name, str(Path.cwd()))
            ws = ws_mgr.get(ws_name)
            disp.info(f"工作空间 '{ws_name}' 已创建")
        else:
            disp.info(f"使用已有工作空间 '{ws_name}'")
    else:
        # 默认使用当前目录名
        cwd = Path.cwd()
        ws_name = cwd.name
        ws = ws_mgr.get(ws_name)
        if not ws:
            ws_mgr.create(ws_name, str(cwd))
            ws = ws_mgr.get(ws_name)
    if ws and not ws.project_path:
        ws.update_project_path(str(Path.cwd()))

    # 通过 _create_workspace_session 创建会话
    _app_ctx.settings = settings
    session, messages = _create_workspace_session(
        ws, disp, _app_ctx, ws_mgr,
        username=args.user,
        session_id=args.session,
        force_new_session=args.new_session,
    )

    # 从 session 解包常用变量
    history_db = session.history_db
    compactor = session.compactor
    bus = _app_ctx.bus
    team_mgr = session.team_mgr
    current_session_id = session.session_id
    current_username = session.username
    cmd = session.cmd
    tool_registry = session.tool_registry

    # 设置 session_id 到 contextvars（用于日志跟踪）
    from .logger import set_session_id
    set_session_id(current_session_id)
    
    # 获取当前工作空间名称
    def _get_workspace_name():
        cwd = Path.cwd()
        return cwd.name
    team_mgr = session.team_mgr

    # 延后设置状态更新回调（需要 messages/history_db 引用）
    def _update_status():
        usage = get_usage()
        disp.status_bar(
            model=session.runtime.settings.model.model,
            context_length=session.runtime.settings.model.context_length,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            system_prompt_tokens=estimate_tokens(messages[0]["content"]) if messages else 0,
            history_count=history_db.count(),
        )
    disp.on_status_update = _update_status

    # 初始状态栏
    disp.status_bar(
        model=session.runtime.settings.model.model,
        context_length=session.runtime.settings.model.context_length,
        prompt_tokens=0,
        completion_tokens=0,
        system_prompt_tokens=estimate_tokens(messages[0]["content"]) if messages else 0,
        history_count=history_db.count(),
    )

    # ── 主循环 ──
    while True:
        user_input = disp.user_input(plan_mode=cmd.plan.state in ('planning', 'awaiting_user', 'awaiting_approval')).strip()
        if not user_input:
            continue

        result = cmd.handle(user_input, messages)
        if result == "break":
            break
        if result == "continue":
            continue
        if result == "approve_plan":
            artifact = cmd.plan.approved_plan
            if artifact:
                parsed_artifact = PlanArtifact.from_dict(artifact)
                if not parsed_artifact:
                    continue
                plan_artifact = PlanService().mark_executing(
                    session_key=current_session_id,
                    sm=cmd,
                    store=PlanStore(history_db, _get_workspace_name(), current_session_id),
                    artifact=parsed_artifact,
                )
                user_input = PlanService().execution_instruction(plan_artifact)
            else:
                continue
        if result == "reload_workspace":
            # 清理旧资源
            _app_ctx.lead_tools_cache = None
            try:
                history_db.close()
            except Exception as e:
                logger.warning(f"[reload] history_db.close() 异常: {e}")
            shutdown_teammates(bus, team_mgr)
            bus.close()

            # 重新加载
            ws_name = _raw.get("active_workspace", "default")
            ws = ws_mgr.get(ws_name)
            if ws:
                session, messages = _create_workspace_session(
                    ws, disp, _app_ctx, ws_mgr)
                history_db = session.history_db
                compactor = session.compactor
                cmd = session.cmd
                bus = _app_ctx.bus
                team_mgr = session.team_mgr
                current_session_id = session.session_id
                current_username = session.username
                tool_registry = session.tool_registry
                disp.info(f"工作空间 '{ws_name}' 已加载（{len(messages) - 1} 条历史）")
            continue

        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        original_user_input = user_input
        planning_turn = cmd.plan.state in ('planning', 'awaiting_user', 'awaiting_approval')
        if planning_turn:
            user_input = build_plan_user_message(user_input, current_plan=cmd.plan.current_plan, selected_option_id=cmd.plan.selected_option_id)
        messages.append({"role": "user", "content": user_input, "timestamp": ts, "_plan_original_content": original_user_input if planning_turn else None})
        workspace_name = _get_workspace_name()
        history_db.append(workspace_name, current_session_id, "user", original_user_input, metadata=json.dumps({"timestamp": ts}))
        disp.user_label(ts)

        try:
            planning_turn = cmd.plan.state in ('planning', 'awaiting_user', 'awaiting_approval')
            tools = filter_tools(_lead_tool_defs(_app_ctx, tool_registry), ToolPolicy.PLAN_READONLY if planning_turn else ToolPolicy.EXECUTION)

            result = ApplicationService().run_turn(
                runtime=session.runtime,
                tools=tools,
                plan_store=PlanStore(history_db, _get_workspace_name(), current_session_id) if planning_turn else None,
                plan_state=cmd,
                user_text_for_history=original_user_input,
                options=RunTurnOptions(
                    streaming=None,
                    plan_turn=planning_turn,
                    context_length=None,
                    persist_user_history=False,
                ),
            )
            msg = result.message

            if planning_turn and result.raw_plan_text:
                artifact = PlanArtifact.from_dict(cmd.plan.current_plan)
                if artifact:
                    cmd.plan.current_plan = artifact.to_dict()
                    cmd.plan.state = artifact.status
                cmd._render_plan()
                disp.info("输入 /act 批准执行，或继续输入修改意见。")

            if cmd.plan.state == 'executing' and msg and msg.get("content"):
                cmd.plan.state = 'completed'

            # 等待队友回禀
            if not (cmd.plan.state in ('planning', 'awaiting_user', 'awaiting_approval')):
                teammate_msg = wait_for_teammates(
                    bus, team_mgr, session.lead_event,
                    cmd.run_tool_fn, messages, _lead_tool_defs(_app_ctx, tool_registry),
                    _inject_todos, disp, history_db=history_db, ctx=cmd.ctx,
                    workspace=_get_workspace_name(), session_id=current_session_id,
                    timeout_settings=session.runtime.settings.timeouts if session.runtime and session.runtime.settings else None,
                )
                if teammate_msg:
                    msg = teammate_msg

            if msg and msg.get("content"):
                ts2 = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                if not any(m.get("role") == "assistant" and m.get("content") == msg["content"] for m in messages[-3:]):
                    messages.append({"role": "assistant", "content": msg["content"],
                                     "timestamp": ts2})

            cleanup_inbox(bus)

            usage = get_usage()
            disp.status_bar(
                model=session.runtime.settings.model.model,
                context_length=session.runtime.settings.model.context_length,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                system_prompt_tokens=estimate_tokens(messages[0]["content"]) if messages else 0,
                history_count=history_db.count(),
            )

            # 使用 Compactor.maybe_compact 统一压缩逻辑
            compactor.maybe_compact(messages, usage["prompt_tokens"], llm_chat, cmd.ctx, session.runtime.settings.model.context_length)

        except KeyboardInterrupt:
            disp.info("⚠ 已中断")
            cleanup_inbox(bus)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n再见")
