"""Agent 执行器 — 统一的 LLM 工具循环"""
import threading

from .config import RUNNER, STREAMING
from .logger import logger

_CONTEXT_USAGE_LIMIT = RUNNER.get("context_usage_limit", 0.85)


def _filter_tools(tool_names: list[str]) -> list[dict]:
    from .tools import get_definitions
    return [d for d in get_definitions() if d["function"]["name"] in tool_names]


def run_tool_loop(
    messages: list[dict],
    tools: list[dict],
    *,
    streaming: bool = False,
    display=None,
    inject_fn=None,
    abort_event: threading.Event | None = None,
    max_turns: int = 30,
    context_length: int | None = None,
    context_usage_limit: float = _CONTEXT_USAGE_LIMIT,
    ctx=None,
) -> tuple[dict | None, bool]:
    """统一工具循环。返回 (final_msg, spawned_teammate)。"""
    from .llm import chat as llm_chat, chat_stream as llm_chat_stream
    from .llm_base import get_usage
    from .tools import handle_tool_calls

    spawned = False

    for turn in range(max_turns):
        if abort_event and abort_event.is_set():
            if display:
                display.text_end()
            return None, spawned

        msg = None

        if streaming:
            thinking_seen = False
            for chunk in llm_chat_stream(messages, tools=tools, ctx=ctx):
                if abort_event and abort_event.is_set():
                    if display:
                        display.text_end()
                    return None, spawned

                if chunk["type"] == "thinking_start":
                    if display:
                        display.thinking_start()
                    thinking_seen = True
                elif chunk["type"] == "thinking":
                    if display:
                        display.thinking_chunk(chunk["content"])
                elif chunk["type"] == "thinking_end":
                    if display:
                        display.thinking_end()
                    thinking_seen = False
                elif chunk["type"] == "text":
                    if display:
                        display.text_chunk(chunk["content"])
                elif chunk["type"] == "error":
                    logger.error(f"[LLM✗] 流式错误: {chunk['error']}")
                    return None, spawned
                elif chunk["type"] == "done":
                    msg = chunk["msg"]

            if thinking_seen and display:
                display.thinking_end()

            if not msg or "tool_calls" not in msg:
                if display:
                    display.text_end()
                return msg, spawned
        else:
            msg = llm_chat(messages, tools=tools, ctx=ctx)

            if msg and msg.get("thinking") and display:
                display.thinking_start()
                display._thinking_buf = msg["thinking"]
                display.thinking_end()

            if not msg or "tool_calls" not in msg:
                if msg and msg.get("content") and display:
                    display.text_end(msg["content"])
                return msg, spawned

        _disp = ctx.display if ctx else display
        tool_spawned = handle_tool_calls(msg, messages, display=_disp)
        if tool_spawned:
            spawned = True

        if inject_fn:
            inject_fn(messages)

        if spawned:
            logger.info("[spawn] lead 退出 LLM 循环，等待队友")
            return None, True

        if context_length is not None:
            usage = get_usage()
            if usage["prompt_tokens"] > context_length * context_usage_limit:
                logger.warning(f"[runner] 上下文将满 prompt_tokens={usage['prompt_tokens']} > {int(context_length * context_usage_limit)}，提前退出")
                return None, spawned

    logger.warning(f"[runner] 工具循环达到上限 {max_turns} 轮，强制退出")
    return None, spawned


def run_agent(messages: list[dict], *, max_turns: int = 10,
              tool_names: list[str] | None = None,
              context_length: int = 128000, ctx=None) -> str | None:
    """轻量 agent 循环（供子代理/队友使用），返回最终文本。"""
    from .tools import get_definitions

    tools = _filter_tools(tool_names) if tool_names else get_definitions()

    msg, _ = run_tool_loop(
        messages, tools,
        streaming=False,
        display=ctx.display if ctx else None,
        inject_fn=None,
        max_turns=max_turns,
        context_length=context_length,
        context_usage_limit=_CONTEXT_USAGE_LIMIT,
        ctx=ctx,
    )
    return msg.get("content") if msg else None
