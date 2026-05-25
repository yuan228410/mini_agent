import threading

from compactor import Compactor
from config import PROJECT_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY
from context import ContextBuilder
from llm import chat, chat_stream, _get_usage
from logger import logger
from memory import MemoryStore
from skills import SkillLoader
from subagents import SubagentLoader
from team_bus import MessageBus
from team_manager import TeammateManager
from session import SessionManager
from team_loop import wait_for_teammates, shutdown_teammates, cleanup_inbox
from display import Display
from tools import get_definitions, handle_tool_calls, register, register_subagents, register_team, register_display, render_todos

SKILL_LOADER = SkillLoader(PROJECT_DIR / "skills")
SUBAGENT_LOADER = SubagentLoader(PROJECT_DIR / "subagents")

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


def _run_tool_loop(messages, tools, inject_fn, disp, max_turns=30):
    for turn in range(max_turns):
        msg = None
        if STREAMING:
            thinking_seen = False
            for chunk in chat_stream(messages, tools=tools):
                if chunk["type"] == "thinking_start":
                    disp.thinking_start()
                    thinking_seen = True
                elif chunk["type"] == "thinking":
                    disp.thinking_chunk(chunk["content"])
                elif chunk["type"] == "thinking_end":
                    disp.thinking_end()
                    thinking_seen = False
                elif chunk["type"] == "text":
                    disp.text_chunk(chunk["content"])
                elif chunk["type"] == "error":
                    logger.error(f"[LLM✗] 流式错误: {chunk['error']}")
                    return None
                elif chunk["type"] == "done":
                    msg = chunk["msg"]
            if thinking_seen:
                disp.thinking_end()
            if not msg or "tool_calls" not in msg:
                disp.text_end()
                return msg
        else:
            msg = chat(messages, tools=tools)
            if msg and msg.get("thinking"):
                disp.thinking_start()
                disp._thinking_buf = msg["thinking"]
                disp.thinking_end()
            if not msg or "tool_calls" not in msg:
                if msg and msg.get("content"):
                    disp.text_end(msg["content"])
                return msg
        spawned = handle_tool_calls(msg, messages)
        inject_fn(messages)
        if spawned:
            logger.info("[spawn] lead 退出 LLM 循环，等待队友")
            return None
    logger.warning(f"[lead] 工具循环达到上限 {max_turns} 轮，强制退出")
    return None


def main():
    disp = Display(
        thinking_mode=DISPLAY.get("thinking_mode", "collapsed"),
        tool_detail=DISPLAY.get("tool_detail", "summary"),
    )

    register(SKILL_LOADER)
    register_subagents(SUBAGENT_LOADER)

    bus = MessageBus(PROJECT_DIR / ".team" / "inbox")
    team_mgr = TeammateManager(
        team_dir=PROJECT_DIR / ".team",
        bus=bus,
        project_dir=PROJECT_DIR,
    )
    register_team(bus, team_mgr)
    register_display(disp)

    lead_event = threading.Event()
    bus.register_wake("lead", lead_event)

    store = MemoryStore(PROJECT_DIR / "memory_data")
    sessions = SessionManager(PROJECT_DIR / "memory_data" / "sessions")
    ctx = ContextBuilder(PROJECT_DIR)

    compactor = Compactor(
        store,
        keep_recent=COMPACTOR["keep_recent"],
        char_threshold=COMPACTOR["char_threshold"],
        context_usage_threshold=COMPACTOR["context_usage_threshold"],
        context_length=MODEL_CONFIG.get("context_length", 128000),
        context_builder=ctx,
        skill_loader=SKILL_LOADER,
    )

    system_prompt = ctx.build(memory_store=store, skill_loader=SKILL_LOADER)
    messages = [{"role": "system", "content": system_prompt}]
    _inject_todos(messages)

    unarchived = store.load_unarchived()
    if unarchived:
        messages.extend(unarchived)
        logger.info(f"[恢复] {len(unarchived)} 条历史消息")

    while True:
        user_input = disp.user_input().strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break
        if user_input.startswith("/save "):
            disp.info(sessions.save(user_input[6:], messages))
            continue
        if user_input.startswith("/load "):
            loaded = sessions.load(user_input[6:])
            if loaded:
                messages = [messages[0]] + loaded
                _inject_todos(messages)
                disp.info(f"会话 '{user_input[6:]}' 已加载（{len(loaded)} 条消息）")
            else:
                disp.error(f"会话 '{user_input[6:]}' 不存在")
            continue
        if user_input == "/sessions":
            disp.info(sessions.render_list())
            continue
        if user_input == "/thinking":
            disp.show_thinking()
            continue
        if user_input.startswith("/thinking "):
            disp.set_thinking_mode(user_input[10:].strip())
            continue

        if user_input == "/compact":
            non_system = [m for m in messages if m["role"] != "system"]
            if len(non_system) <= compactor.keep_recent:
                disp.info(f"消息数({len(non_system)})未超过保留阈值({compactor.keep_recent})，无需压缩")
                continue
            before = len(non_system)
            messages = compactor.compact(chat, messages)
            _inject_todos(messages)
            after = len([m for m in messages if m["role"] != "system"])
            disp.info(f"压缩完成：{before} → {after} 条消息（归档 {before - after} 条）")
            continue

        messages.append({"role": "user", "content": user_input})
        store.append("user", user_input)

        msg = _run_tool_loop(messages, _lead_tool_defs(), _inject_todos, disp)

        teammate_msg = wait_for_teammates(
            bus, team_mgr, lead_event,
            _run_tool_loop, messages, _lead_tool_defs(),
            _inject_todos, disp, store,
        )
        if teammate_msg:
            msg = teammate_msg

        if msg and msg.get("content"):
            messages.append({"role": "assistant", "content": msg["content"]})
            store.append("assistant", msg["content"])

        shutdown_teammates(bus, team_mgr)
        cleanup_inbox(bus)

        if compactor.should_compact(_get_usage()["prompt_tokens"]):
            messages = compactor.compact(chat, messages)
            _inject_todos(messages)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n再见")
