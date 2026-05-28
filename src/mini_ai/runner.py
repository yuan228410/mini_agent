"""Agent 执行器 — 统一的 LLM 工具循环"""
import threading
from datetime import datetime, timezone, timedelta

_UTC8 = timezone(timedelta(hours=8))
def _now(): return datetime.now(_UTC8).strftime("%Y-%m-%dT%H:%M:%S")

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
    persist_fn=None,
    abort_event: threading.Event | None = None,
    max_turns: int = 0,
    context_length: int | None = None,
    context_usage_limit: float = _CONTEXT_USAGE_LIMIT,
    ctx=None,
) -> tuple[dict | None, bool]:
    """统一工具循环。返回 (final_msg, spawned_teammate)。
    persist_fn: 可选回调 (msg_dict) -> None，每条新消息追加时调用。"""
    from .llm import chat as llm_chat, chat_stream as llm_chat_stream, get_usage
    from .tools import handle_tool_calls

    spawned = False
    _persist = persist_fn or (lambda m: None)

    if max_turns <= 0:
        from .config import RUNNER
        max_turns = RUNNER.get("max_turns", 20)
    _consecutive_errors = 0
    try:
        for turn in range(max_turns):
            if abort_event and abort_event.is_set():
                if display:
                    display.text_end()
                return None, spawned

            msg = None

            if streaming:
                thinking_seen = False
                for chunk in llm_chat_stream(messages, tools=tools, ctx=ctx, abort_event=abort_event):
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
                        if display:
                            display.text_end()
                            display.tool_result("error", f"⚠ LLM 错误: {chunk['error']}", elapsed=0)
                        return None, spawned
                    elif chunk["type"] == "done":
                        msg = chunk["msg"]

                if thinking_seen and display:
                    display.thinking_end()

                if not msg or "tool_calls" not in msg:
                    if msg:
                        msg["timestamp"] = _now()
                        _persist(msg)
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
                    if msg:
                        msg["timestamp"] = _now()
                        _persist(msg)
                    if msg and msg.get("content") and display:
                        display.text_end(msg["content"])
                    return msg, spawned

            _disp = ctx.display if ctx else display
            tool_spawned = handle_tool_calls(msg, messages, display=_disp, persist_fn=_persist)
            last_tool_msgs = [m for m in messages[-5:] if m.get("role") == "tool"]
            if last_tool_msgs and last_tool_msgs[-1].get("content", "").startswith("Error:"):
                _consecutive_errors += 1
                if _consecutive_errors >= 3:
                    logger.warning(f"[runner] 连续 {_consecutive_errors} 次工具错误，提前退出")
                    if display:
                        display.text_end()
                    return msg, spawned
            else:
                _consecutive_errors = 0
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
                    if display:
                        display.text_end()
                    return None, spawned

        # 记录每轮工具调用详情，便于排查循环原因
        tool_summary = []
        for m in messages[-max_turns*3:]:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tool_summary.append(tc["function"]["name"])
            elif m.get("role") == "tool":
                content = (m.get("content") or "")[:60].replace("\n", " ")
                tool_summary.append(f"→{content}")
        logger.warning(f"[runner] 工具循环达到上限 {max_turns} 轮，强制退出。最近工具: {' | '.join(tool_summary[-20:])}")
        if display:
            display.text_end()
        return None, spawned
    except KeyboardInterrupt:
        if display:
            display.text_end()
        logger.info("[runner] 用户中断 (Ctrl+C)")
        return None, spawned
    except Exception as e:
        logger.error(f"[runner] 未预期异常: {e}", exc_info=True)
        if display:
            display.text_end()
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
