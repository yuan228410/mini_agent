import argparse
import threading
from pathlib import Path

from . import __version__
from .cli import CommandHandler, Display
from .memory import MemoryStore, Compactor, SessionManager
from .memory.history_db import HistoryDB
from datetime import datetime
from .config import DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY, SKILL_PATHS, PLAN, MCP, RequestContext, _raw
from .context import ContextBuilder
from .llm import get_usage, reset_usage
from .logger import logger
from .runner import run_tool_loop
from .skills import SkillLoader
from .subagents import SubagentLoader
from .team import MessageBus, TeammateManager, Blackboard
from .team.loop import wait_for_teammates, cleanup_inbox
from .tools import get_definitions, register, register_subagents, register_team, register_display, register_blackboard, register_memory_tools, register_history_tools, render_todos
from .workspace import WorkspaceManager

SKILL_LOADER = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
SUBAGENT_LOADER = SubagentLoader(PACKAGE_DIR / "subagents")

_MCP_LOADER = None

_LEAD_TOOLS = None



def _init_mcp():
    global _MCP_LOADER, _LEAD_TOOLS
    if not MCP.get("enabled") or not MCP.get("servers"):
        return
    try:
        from .tools.mcp_loader import MCPLoader
    except ImportError:
        logger.warning("[MCP] mcp 包未安装，跳过 MCP 初始化 (pip install mcp)")
        return
    _MCP_LOADER = MCPLoader()
    modules = _MCP_LOADER.start_sync()
    if modules:
        from .tools import _registry
        _registry.add_tools(*modules)
        _LEAD_TOOLS = None
        logger.info(f"[MCP] 已注册 {len(modules)} 个 MCP 工具")

def _shutdown_mcp():
    global _MCP_LOADER
    if _MCP_LOADER:
        _MCP_LOADER.stop_sync()
        _MCP_LOADER = None

def _lead_tool_defs() -> list[dict]:
    global _LEAD_TOOLS
    if _LEAD_TOOLS is None:
        _LEAD_TOOLS = [d for d in get_definitions() if d["function"]["name"] not in ("read_inbox", "list_teammates")]
    return _LEAD_TOOLS


def _inject_todos(messages: list[dict]):
    todos_text = render_todos()
    base = messages[0]["content"]
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[: base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"


def _run_loop_compat(messages, tools, inject_fn, disp, ctx=None):
    """兼容包装：保持 CommandHandler 和 wait_for_teammates 的调用签名。"""
    msg, _ = run_tool_loop(
        messages, tools,
        streaming=STREAMING,
        display=disp,
        inject_fn=inject_fn,
        ctx=ctx,
    )
    return msg


def main():
    parser = argparse.ArgumentParser(prog="mini-ai", description="智能对话 Agent")
    parser.add_argument("-v", "--version", action="version", version=f"mini-ai {__version__}")
    parser.add_argument("--web", action="store_true", help="启动 Web 界面")
    parser.add_argument("--port", type=int, default=8765, help="Web 端口 (默认 8765)")
    args = parser.parse_args()

    if args.web:
        from .web.app import create_app
        import uvicorn
        dist_dir = __import__("pathlib").Path(__file__).parent.parent.parent / "web" / "dist"
        if not dist_dir.exists():
            print(f"提示: 前端未构建，请先执行: cd web && pnpm install && pnpm build")
            print(f"      开发模式可分别启动后端和前端 (pnpm dev)")
        print(f"mini_ai Web 界面启动: http://localhost:{args.port}")
        import signal
        app = create_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=args.port, timeout_graceful_shutdown=3)
        server = uvicorn.Server(config)
        original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda sig, frame: signal.raise_signal(signal.SIGTERM))
        try:
            server.run()
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGINT, original_sigint)
        return
    def _update_status():
        usage = get_usage()
        disp.status_bar(
            model=MODEL_CONFIG.get("model", "?"),
            context_length=MODEL_CONFIG.get("context_length", 128000),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            system_prompt_chars=len(messages[0]["content"]) if messages else 0,
            history_count=len(history_db.load_unarchived()),
        )

    disp = Display(
        thinking_mode=DISPLAY.get("thinking_mode", "collapsed"),
        tool_detail=DISPLAY.get("tool_detail", "summary"),
        on_status_update=_update_status,
    )

    ws_mgr = WorkspaceManager(DATA_DIR, ensure_default=False)
    cwd = Path.cwd()
    cwd_name = cwd.name
    ws = ws_mgr.get(cwd_name)
    if not ws:
        ws_mgr.create(cwd_name, str(cwd))
        ws = ws_mgr.get(cwd_name)
    if ws and not ws.project_path:
        ws.update_project_path(str(cwd))
    ws_dir = ws.ws_dir
    logger.info(f"[Workspace] {cwd_name} → {ws_dir} (project: {ws.project_path or cwd})")

    register(SKILL_LOADER)
    register_subagents(SUBAGENT_LOADER)

    bus = MessageBus(ws_dir / ".team" / "inbox")
    team_mgr = TeammateManager(
        team_dir=ws_dir / ".team",
        bus=bus,
        project_dir=ws_dir,
    )
    register_team(bus, team_mgr)
    register_display(disp)

    _init_mcp()

    bb = Blackboard(persist_path=ws_dir / ".team" / "blackboard.json")
    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
    register_blackboard(bb, workflow_dirs=workflow_dirs)

    req_ctx = RequestContext(model_config=MODEL_CONFIG, display=disp)

    lead_event = threading.Event()
    bus.register_wake("lead", lead_event)

    store = MemoryStore(ws_dir / "memory_data")
    history_db = HistoryDB(ws_dir / "memory_data" / "history.db", workspace=cwd_name)
    sessions = SessionManager(ws_dir / "memory_data" / "sessions")
    register_memory_tools(store)
    register_history_tools(history_db)
    ctx = ContextBuilder(DATA_DIR)

    compactor = Compactor(
        store,
        keep_recent=COMPACTOR["keep_recent"],
        char_threshold=COMPACTOR["char_threshold"],
        context_usage_threshold=COMPACTOR["context_usage_threshold"],
        context_length=MODEL_CONFIG.get("context_length", 128000),
        context_builder=ctx,
        skill_loader=SKILL_LOADER,
        history_db=history_db,
    )

    cmd = CommandHandler(
        disp=disp, store=store, sessions=sessions, compactor=compactor,
        inject_fn=_inject_todos, run_tool_fn=_run_loop_compat,
        lead_tools=_lead_tool_defs(), ctx=req_ctx, workspace_mgr=ws_mgr,
        history_db=history_db,
    )

    system_prompt = ctx.build(memory_store=store, skill_loader=SKILL_LOADER, project_path=ws.project_path)
    messages = [{"role": "system", "content": system_prompt}]
    _inject_todos(messages)

    unarchived = history_db.load_unarchived()
    if unarchived:
        messages.extend(unarchived)
        logger.info(f"[恢复] {len(unarchived)} 条历史消息")

    disp.status_bar(
        model=MODEL_CONFIG.get("model", "?"),
        context_length=MODEL_CONFIG.get("context_length", 128000),
        prompt_tokens=0,
        completion_tokens=0,
        system_prompt_chars=len(messages[0]["content"]) if messages else 0,
        history_count=len(unarchived),
    )

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
            ws_name = _raw.get("active_workspace", "default")
            ws = ws_mgr.get(ws_name)
            if ws:
                ws_dir = ws.ws_dir
                store = MemoryStore(ws_dir / "memory_data")
                history_db = HistoryDB(ws_dir / "memory_data" / "history.db", workspace=ws_name)
                sessions = SessionManager(ws_dir / "memory_data" / "sessions")
                system_prompt = ctx.build(memory_store=store, skill_loader=SKILL_LOADER, project_path=ws.project_path)
                messages = [{"role": "system", "content": system_prompt}]
                _inject_todos(messages)
                unarchived = history_db.load_unarchived()
                if unarchived:
                    messages.extend(unarchived)
                cmd.store = store
                cmd.sessions = sessions
                disp.info(f"工作空间 '{ws_name}' 已加载（{len(unarchived)} 条历史）")
            continue

        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        messages.append({"role": "user", "content": user_input, "timestamp": ts})
        history_db.append("user", user_input)
        disp.user_label(ts)

        try:
            reset_usage()
            tools = [] if cmd.plan_mode else _lead_tool_defs()
            msg, _ = run_tool_loop(
                messages, tools,
                streaming=STREAMING,
                display=disp,
                inject_fn=_inject_todos,
                ctx=req_ctx,
            )

            if cmd.plan_mode and msg and msg.get("content"):
                if PLAN.get("approval", True):
                    msg["content"] += "\n\n📋 以上为执行计划，确认后输入 /act 开始执行"
                else:
                    cmd.plan_mode = False
                    msg["content"] += "\n\n⚡ 已自动切换到执行模式"
                    tools = _lead_tool_defs()
                    msg2, _ = run_tool_loop(
                        messages, tools,
                        streaming=STREAMING,
                        display=disp,
                        inject_fn=_inject_todos,
                        ctx=req_ctx,
                    )
                    if msg2 and msg2.get("content"):
                        msg = msg2

            if not cmd.plan_mode:
                teammate_msg = wait_for_teammates(
                    bus, team_mgr, lead_event,
                    _run_loop_compat, messages, _lead_tool_defs(),
                    _inject_todos, disp, history_db=history_db, ctx=req_ctx,
                )
                if teammate_msg:
                    msg = teammate_msg

            if msg and msg.get("content"):
                ts2 = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                messages.append({"role": "assistant", "content": msg["content"], "timestamp": ts2})
                history_db.append("assistant", msg["content"])

            cleanup_inbox(bus)

            usage = get_usage()
            disp.status_bar(
                model=MODEL_CONFIG.get("model", "?"),
                context_length=MODEL_CONFIG.get("context_length", 128000),
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                system_prompt_chars=len(messages[0]["content"]) if messages else 0,
                history_count=len(history_db.load_unarchived()),
            )

            if compactor.should_compact(usage["prompt_tokens"]) or compactor.should_compact_local(messages):
                from .llm import chat
                messages = compactor.compact(chat, messages)
                _inject_todos(messages)
        except KeyboardInterrupt:
            disp.info("⚠ 已中断")
            cleanup_inbox(bus)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n再见")
