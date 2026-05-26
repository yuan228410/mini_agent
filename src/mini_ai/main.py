import argparse
import threading

from . import __version__
from .cli import CommandHandler, Display
from .memory import MemoryStore, Compactor, SessionManager
from .config import DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY, SKILL_PATHS, RequestContext
from .context import ContextBuilder
from .llm import get_usage
from .logger import logger
from .runner import run_tool_loop
from .skills import SkillLoader
from .subagents import SubagentLoader
from .team import MessageBus, TeammateManager, Blackboard
from .team.loop import wait_for_teammates, cleanup_inbox
from .tools import get_definitions, register, register_subagents, register_team, register_display, register_blackboard, render_todos

SKILL_LOADER = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
SUBAGENT_LOADER = SubagentLoader(PACKAGE_DIR / "subagents")

_LEAD_TOOLS = None


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
        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=args.port)
        return
    disp = Display(
        thinking_mode=DISPLAY.get("thinking_mode", "collapsed"),
        tool_detail=DISPLAY.get("tool_detail", "summary"),
    )

    register(SKILL_LOADER)
    register_subagents(SUBAGENT_LOADER)

    bus = MessageBus(DATA_DIR / ".team" / "inbox")
    team_mgr = TeammateManager(
        team_dir=DATA_DIR / ".team",
        bus=bus,
        project_dir=DATA_DIR,
    )
    register_team(bus, team_mgr)
    register_display(disp)

    bb = Blackboard(persist_path=DATA_DIR / ".team" / "blackboard.json")
    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
    register_blackboard(bb, workflow_dirs=workflow_dirs)

    req_ctx = RequestContext(model_config=MODEL_CONFIG, display=disp)

    lead_event = threading.Event()
    bus.register_wake("lead", lead_event)

    store = MemoryStore(DATA_DIR / "memory_data")
    sessions = SessionManager(DATA_DIR / "memory_data" / "sessions")
    ctx = ContextBuilder(DATA_DIR)

    compactor = Compactor(
        store,
        keep_recent=COMPACTOR["keep_recent"],
        char_threshold=COMPACTOR["char_threshold"],
        context_usage_threshold=COMPACTOR["context_usage_threshold"],
        context_length=MODEL_CONFIG.get("context_length", 128000),
        context_builder=ctx,
        skill_loader=SKILL_LOADER,
    )

    cmd = CommandHandler(
        disp=disp, store=store, sessions=sessions, compactor=compactor,
        inject_fn=_inject_todos, run_tool_fn=_run_loop_compat,
        lead_tools=_lead_tool_defs(), ctx=req_ctx,
    )

    system_prompt = ctx.build(memory_store=store, skill_loader=SKILL_LOADER)
    messages = [{"role": "system", "content": system_prompt}]
    _inject_todos(messages)

    unarchived = store.load_unarchived()
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
        user_input = disp.user_input().strip()
        if not user_input:
            continue

        result = cmd.handle(user_input, messages)
        if result == "break":
            break
        if result == "continue":
            continue

        messages.append({"role": "user", "content": user_input})
        store.append("user", user_input)

        msg, _ = run_tool_loop(
            messages, _lead_tool_defs(),
            streaming=STREAMING,
            display=disp,
            inject_fn=_inject_todos,
            ctx=req_ctx,
        )

        teammate_msg = wait_for_teammates(
            bus, team_mgr, lead_event,
            _run_loop_compat, messages, _lead_tool_defs(),
            _inject_todos, disp, store, ctx=req_ctx,
        )
        if teammate_msg:
            msg = teammate_msg

        if msg and msg.get("content"):
            messages.append({"role": "assistant", "content": msg["content"]})
            store.append("assistant", msg["content"])

        cleanup_inbox(bus)

        usage = get_usage()
        disp.status_bar(
            model=MODEL_CONFIG.get("model", "?"),
            context_length=MODEL_CONFIG.get("context_length", 128000),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            system_prompt_chars=len(messages[0]["content"]) if messages else 0,
            history_count=len(store.load_unarchived()),
        )

        if compactor.should_compact(usage["prompt_tokens"]):
            from .llm import chat
            messages = compactor.compact(chat, messages)
            _inject_todos(messages)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n再见")
