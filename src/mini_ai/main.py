import json
import argparse
import threading
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

from . import __version__
from .cli import CommandHandler, Display
from .memory import MemoryStore, Compactor, HistoryDB, HistoryDBPool
from .config import (DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING,
                     DISPLAY, SKILL_PATHS, PLAN, MCP, RequestContext, _raw, user_data_dir)
from .context import ContextBuilder
from .llm import get_usage, reset_usage, estimate_tokens
from .logger import logger
from .runner import run_tool_loop
from .skills import SkillLoader
from .subagents import SubagentLoader
from .team import MessageBus, TeammateManager, Blackboard
from .team.loop import wait_for_teammates, shutdown_teammates, cleanup_inbox
from .tools import (get_definitions, register, register_subagents, register_team,
                    register_display, register_blackboard, register_memory_tools,
                    register_history_tools, render_todos, set_project_path)
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


_app_ctx = AppContext()
_subagent_loader = SubagentLoader(PACKAGE_DIR / "subagents")


def get_app_context() -> AppContext:
    """供外部模块获取全局 AppContext（如 commands.py 的 /mcp 命令）。"""
    return _app_ctx


# ═══════════════════════════════════════════
# MCP 初始化 / 关闭
# ═══════════════════════════════════════════

def _init_mcp(ctx: AppContext):
    if not MCP.get("enabled") or not MCP.get("servers"):
        return
    try:
        from .tools.mcp_loader import MCPLoader
    except ImportError:
        logger.warning("[MCP] mcp 包未安装，跳过 MCP 初始化 (pip install mcp)")
        return
    ctx.mcp_loader = MCPLoader()
    modules = ctx.mcp_loader.start_sync()
    if modules:
        from .tools import _registry
        _registry.add_tools(*modules)
        ctx.lead_tools_cache = None
        logger.info(f"[MCP] 已注册 {len(modules)} 个 MCP 工具")


def _shutdown_mcp(ctx: AppContext):
    if ctx.mcp_loader:
        ctx.mcp_loader.stop_sync()
        ctx.mcp_loader = None


# ═══════════════════════════════════════════
# 工具辅助
# ═══════════════════════════════════════════

def _lead_tool_defs(ctx: AppContext) -> list[dict]:
    if ctx.lead_tools_cache is None:
        ctx.lead_tools_cache = [
            d for d in get_definitions()
            if d["function"]["name"] not in ("read_inbox", "list_teammates")
        ]
    return ctx.lead_tools_cache


def _inject_todos(messages: list[dict]):
    todos_text = render_todos()
    base = messages[0]["content"]
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[:base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"


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
    cmd: CommandHandler = None  # 主循环 handler，reload 解包时赋予


def _create_workspace_session(
    ws,
    disp: Display,
    app_ctx: AppContext,
    ws_mgr: WorkspaceManager,
    username: str = "default",
    session_id: str | None = None,
) -> tuple[SessionContext, list[dict]]:
    """为指定 workspace 创建完整的会话状态，返回 (session, messages, cmd_handler)。"""
    import uuid
    ws_dir = ws.ws_dir
    ws_name = ws.name

    logger.info(f"[Workspace] {ws_name} → {ws_dir} (project: {ws.project_path or Path.cwd()})")

    set_project_path(ws.project_path or str(Path.cwd()))

    # ── 技能 ──
    user_skills_dir = user_data_dir(username) / "skills"
    ws_skills_dir = ws_dir / "skills"
    skill_loader = SkillLoader(DATA_DIR / "skills", SKILL_PATHS,
                               user_skills_dir=user_skills_dir,
                               workspace_skills_dir=ws_skills_dir)
    register(skill_loader)
    register_subagents(_subagent_loader)

    # ── Team + Bus ──
    bus = MessageBus(ws_dir / ".team" / "inbox")
    app_ctx.bus = bus
    team_mgr = TeammateManager(team_dir=ws_dir / ".team", bus=bus, project_dir=ws_dir)
    register_team(bus, team_mgr)
    register_display(disp)

    # ── MCP ──
    _init_mcp(app_ctx)

    # ── Blackboard + Workflow ──
    bb = Blackboard(persist_path=ws_dir / ".team" / "blackboard.json")
    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
    register_blackboard(bb, workflow_dirs=workflow_dirs, bus=bus, manager=team_mgr)

    lead_event = threading.Event()
    bus.register_wake("lead", lead_event)

    # ── 记忆 + 历史 ──
    global_memory_dir = DATA_DIR / "memory"
    user_memory_dir = user_data_dir(username) / "memory"
    ws_memory_dir = ws_dir / "memory_data"
    
    # 先创建 history_db（用于检查会话是否存在）
    history_db = HistoryDBPool.get(username)  # 使用指定用户
    
    # 生成会话 ID（如果未指定）
    if not session_id:
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        logger.info(f"[会话] 创建新会话: {session_id}")
        disp.info(f"创建新会话: {session_id}")
    else:
        # 指定了会话ID，检查是否存在
        existing = history_db.load_session(ws_name, session_id, limit=1)
        if existing:
            logger.info(f"[会话] 恢复会话: {session_id}")
            disp.info(f"恢复会话: {session_id}")
        else:
            # 不存在，创建新会话（使用指定的ID）
            logger.info(f"[会话] 会话 {session_id} 不存在，创建新会话")
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
    register_memory_tools(store)
    register_history_tools(history_db, ws_name)

    # ── Context ──
    ctx_builder = ContextBuilder(DATA_DIR)

    # ── Compactor ──
    compactor = Compactor(
        store,
        keep_recent=COMPACTOR.get("keep_recent", 50),
        context_usage_threshold=COMPACTOR.get("context_usage_threshold", 0.8),
        keep_budget_ratio=COMPACTOR.get("keep_budget_ratio", 0.2),
        early_compact_ratio=COMPACTOR.get("early_compact_ratio", 0.85),
        max_cached_summaries=COMPACTOR.get("max_cached_summaries", 200),
        max_summary_sections=COMPACTOR.get("max_summary_sections", 50),
        context_length=MODEL_CONFIG.get("context_length", 256000),
        context_builder=ctx_builder,
        skill_loader=skill_loader,
        project_path=ws.project_path or str(Path.cwd()),
        summary_dir=session_dir,
    )

    # ── CommandHandler（含 run_loop 闭包）──
    req_ctx = RequestContext(model_config=MODEL_CONFIG, display=disp)

    def _run_loop(messages, tools, inject_fn, disp, ctx=None):
        msg, _ = run_tool_loop(
            messages, tools,
            streaming=STREAMING,
            display=disp,
            inject_fn=inject_fn,
            ctx=ctx,
            bus=app_ctx.bus,
        )
        return msg

    cmd = CommandHandler(
        disp=disp, store=store, compactor=compactor,
        inject_fn=_inject_todos, run_tool_fn=_run_loop,
        lead_tools=_lead_tool_defs(app_ctx), ctx=req_ctx, workspace_mgr=ws_mgr,
        history_db=history_db, context_builder=ctx_builder, skill_loader=skill_loader,
        project_path=ws.project_path, username=username, session_id=session_id,
    )

    # ── 构建消息 ──
    system_prompt = ctx_builder.build(memory_store=store, skill_loader=skill_loader,
                                       project_path=ws.project_path)
    messages = [{"role": "system", "content": system_prompt}]
    _inject_todos(messages)

    # 加载指定会话的历史消息
    ctx_limit = COMPACTOR.get("context_limit", 50)
    restored = history_db.load_session(ws_name, session_id, limit=ctx_limit)
    if restored:
        messages.extend(restored)
        logger.info(f"[恢复] 会话 {session_id} 的 {len(restored)} 条历史消息")

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
        import signal
        app = create_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=args.port,
                                timeout_graceful_shutdown=3)
        server = uvicorn.Server(config)
        original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT,
                      lambda sig, frame: signal.raise_signal(signal.SIGTERM))
        try:
            server.run()
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGINT, original_sigint)
        return

    # ── CLI 模式 ──
    # 使用用户数据目录（与 Web 模式一致）
    from .config import user_data_dir
    user_root = user_data_dir(args.user)
    ws_mgr = WorkspaceManager(user_root, ensure_default=False)

    # 显示
    disp = Display(
        thinking_mode=DISPLAY.get("thinking_mode", "collapsed"),
        tool_detail=DISPLAY.get("tool_detail", "summary"),
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
    session, messages = _create_workspace_session(
        ws, disp, _app_ctx, ws_mgr,
        username=args.user,
        session_id=None if args.new_session else args.session,
    )

    # 从 session 解包常用变量
    history_db = session.history_db
    compactor = session.compactor
    bus = _app_ctx.bus
    team_mgr = session.team_mgr
    current_session_id = session.session_id
    current_username = session.username
    cmd = session.cmd
    
    # 获取当前工作空间名称
    def _get_workspace_name():
        cwd = Path.cwd()
        return cwd.name
    team_mgr = session.team_mgr

    # 延后设置状态更新回调（需要 messages/history_db 引用）
    def _update_status():
        usage = get_usage()
        disp.status_bar(
            model=MODEL_CONFIG.get("model", "?"),
            context_length=MODEL_CONFIG.get("context_length", 256000),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            system_prompt_tokens=estimate_tokens(messages[0]["content"]) if messages else 0,
            history_count=history_db.count(),
        )
    disp.on_status_update = _update_status

    # 初始状态栏
    disp.status_bar(
        model=MODEL_CONFIG.get("model", "?"),
        context_length=MODEL_CONFIG.get("context_length", 256000),
        prompt_tokens=0,
        completion_tokens=0,
        system_prompt_tokens=estimate_tokens(messages[0]["content"]) if messages else 0,
        history_count=history_db.count(),
    )

    # ── 主循环 ──
    while True:
        user_input = disp.user_input(plan_mode=cmd.plan_mode).strip()
        if not user_input:
            continue

        result = cmd.handle(user_input, messages)
        if result == "break":
            break
        if result == "continue":
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
                disp.info(f"工作空间 '{ws_name}' 已加载（{len(messages) - 1} 条历史）")
            continue

        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        messages.append({"role": "user", "content": user_input, "timestamp": ts})
        workspace_name = _get_workspace_name()
        history_db.append(workspace_name, current_session_id, "user", user_input, metadata=json.dumps({"timestamp": ts}))
        disp.user_label(ts)

        try:
            reset_usage()
            tools = [] if cmd.plan_mode else _lead_tool_defs(_app_ctx)

            _cli_deferred = []

            def _persist(m):
                ws_name = _get_workspace_name()
                if m["role"] == "tool":
                    history_db.append(ws_name, current_session_id, "tool", m.get("content", ""),
                                      metadata=json.dumps({"name": m.get("name", ""),
                                                           "tool_call_id": m.get("tool_call_id", "")}))
                elif m["role"] == "assistant":
                    if m.get("tool_calls"):
                        _cli_deferred.append(m)
                    else:
                        meta = {"thinking": m["thinking"]} if m.get("thinking") else {}
                        history_db.append(ws_name, current_session_id, "assistant", m.get("content", ""),
                                          metadata=json.dumps(meta) if meta else "")

            msg, _ = run_tool_loop(
                messages, tools,
                streaming=STREAMING,
                display=disp,
                inject_fn=_inject_todos,
                ctx=cmd.ctx,
                persist_fn=_persist,
                bus=bus,
            )
            _flush_deferred(history_db, messages, _cli_deferred, _get_workspace_name(), current_session_id)  # 异常时跳过，避免写入不完整消息

            # 计划模式自动执行
            if cmd.plan_mode and msg and msg.get("content"):
                if PLAN.get("approval", True):
                    msg["content"] += "\n\n📋 以上为执行计划，确认后输入 /act 开始执行"
                else:
                    cmd.plan_mode = False
                    msg["content"] += "\n\n⚡ 已自动切换到执行模式"
                    tools = _lead_tool_defs(_app_ctx)
                    msg2, _ = run_tool_loop(
                        messages, tools,
                        streaming=STREAMING,
                        display=disp,
                        inject_fn=_inject_todos,
                        ctx=cmd.ctx,
                        persist_fn=_persist,
                        bus=bus,
                    )
                    _flush_deferred(history_db, messages, _cli_deferred, _get_workspace_name(), current_session_id)
                    if msg2 and msg2.get("content"):
                        msg = msg2

            # 等待队友回禀
            if not cmd.plan_mode:
                teammate_msg = wait_for_teammates(
                    bus, team_mgr, session.lead_event,
                    cmd.run_tool_fn, messages, _lead_tool_defs(_app_ctx),
                    _inject_todos, disp, history_db=history_db, ctx=cmd.ctx,
                    workspace=_get_workspace_name(), session_id=current_session_id,
                )
                if teammate_msg:
                    msg = teammate_msg

            if msg and msg.get("content"):
                ts2 = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                messages.append({"role": "assistant", "content": msg["content"],
                                 "timestamp": ts2})

            cleanup_inbox(bus)

            usage = get_usage()
            disp.status_bar(
                model=MODEL_CONFIG.get("model", "?"),
                context_length=MODEL_CONFIG.get("context_length", 256000),
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                system_prompt_tokens=estimate_tokens(messages[0]["content"]) if messages else 0,
                history_count=history_db.count(),
            )

            if compactor.should_compact(usage["prompt_tokens"]) or compactor.should_compact_local(messages):
                logger.info(f"[CLI] 触发压缩: prompt_tokens={usage['prompt_tokens']}, messages={len(messages)}")
                from .llm import chat
                messages = compactor.compact(chat, messages, ctx=cmd.ctx, inject_fn=_inject_todos)
                logger.info(f"[CLI] 压缩完成: messages={len(messages)}")

        except KeyboardInterrupt:
            disp.info("⚠ 已中断")
            cleanup_inbox(bus)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n再见")
