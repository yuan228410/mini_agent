"""Chat assistant response and follow-up helpers."""
from __future__ import annotations

from typing import Any

from ..core.events import DisplayEvent, DisplayEventType
from ..core.runtime_types import HistoryDBProtocol, MessageBusProtocol, MessageDict, UsageDict
from ..utils import now_ts
from .chat_models import ChatAssistantDispatch, ChatErrorResult, RunTurnResult, TeamFollowupTiming, TeamInboxInjectionResult

PLAN_DISCUSSION_DISPLAY_CONTENT = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
TEAM_FOLLOWUP_INSTRUCTION = "队友回禀已收到。请先 blackboard_read 获取队友写入黑板的结果，再基于回禀和黑板内容回复用户。"


def valid_assistant_result(message: MessageDict | None) -> bool:
    """Return whether an assistant message has content or tool calls."""

    return bool(message and (message.get("content") or message.get("tool_calls")))


def finalize_chat_assistant_response(
    *,
    messages: list[MessageDict],
    result: RunTurnResult,
    plan_turn: bool,
) -> ChatAssistantDispatch:
    """Persist/convert a successful assistant result and return display metadata."""

    message = result.message
    display_content = None
    if message and message.get("content") and message["content"].strip():
        if plan_turn:
            display_content = apply_plan_discussion_response(messages, message, result.raw_plan_text)
        else:
            append_chat_assistant_message(messages, message)
    return ChatAssistantDispatch(message=message, usage=result.usage, display_content=display_content)


def fallback_error_text(message: MessageDict | None) -> str:
    """Return the assistant-visible fallback error text for an invalid model response."""

    if message is None:
        return "⚠ LLM 未返回有效回复（可能因限流或错误）"
    if message.get("interrupted"):
        return "⏸ 生成已中断"
    if message.get("error"):
        return f"⚠ {message.get('error')}"
    return "⚠ LLM 未返回有效回复（可能因限流或错误）"


def chat_error_context(*, session_id: str, workspace: str | None, messages: list[MessageDict]) -> dict[str, Any]:
    """Build diagnostic context for a failed chat turn."""

    context: dict[str, Any] = {
        "session_id": session_id,
        "workspace": workspace,
        "message_count": len(messages),
        "last_user_message": None,
        "last_tool_calls": [],
    }
    for message in reversed(messages[-5:]):
        if message.get("role") == "user":
            context["last_user_message"] = str(message.get("content", ""))[:200]
            break
    for message in reversed(messages[-10:]):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            context["last_tool_calls"] = [
                {"name": tool_call.get("function", {}).get("name"), "id": tool_call.get("id")}
                for tool_call in message["tool_calls"][:3]
            ]
            break
    return context


def handle_invalid_chat_result(
    *,
    message: MessageDict | None,
    messages: list[MessageDict],
    history_db: HistoryDBProtocol,
    workspace: str,
    history_session_id: str,
    event_session_id: str,
    usage: UsageDict,
    timestamp: str | None = None,
) -> ChatErrorResult:
    """Append/persist a fallback assistant error and build the complete event."""

    err_text = fallback_error_text(message)
    messages.append({"role": "assistant", "content": err_text, "timestamp": timestamp or now_ts()})
    history_db.append(workspace, history_session_id, "assistant", err_text)
    event = DisplayEvent(
        DisplayEventType.COMPLETE,
        {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "error": err_text,
            "error_context": chat_error_context(session_id=event_session_id, workspace=workspace, messages=messages),
            "session_id": event_session_id,
        },
    ).to_wire()
    return ChatErrorResult(message=message, usage=usage, event=event, error_text=err_text)


def apply_plan_discussion_response(messages: list[MessageDict], message: MessageDict, raw_plan_text: str | None) -> str:
    """Replace raw plan artifact text with display-friendly plan discussion content."""

    message["content"] = PLAN_DISCUSSION_DISPLAY_CONTENT
    message["kind"] = "plan_discussion"
    for existing in reversed(messages):
        if existing.get("role") == "assistant" and existing.get("content") == raw_plan_text:
            existing["content"] = PLAN_DISCUSSION_DISPLAY_CONTENT
            existing["kind"] = "plan_discussion"
            break
    return PLAN_DISCUSSION_DISPLAY_CONTENT


def append_chat_assistant_message(messages: list[MessageDict], message: MessageDict, *, timestamp: str | None = None) -> MessageDict | None:
    """Append a normal assistant response unless the runner already persisted it."""

    content = message.get("content")
    if not content or not str(content).strip():
        return None
    if any(existing.get("role") == "assistant" and existing.get("content") == content for existing in messages[-3:]):
        return None

    assistant_message = {
        "role": "assistant",
        "content": content,
        "thinking": message.get("thinking"),
        "timestamp": timestamp or now_ts(),
        "kind": "chat",
    }
    messages.append(assistant_message)
    return assistant_message


def should_poll_team_followup(bus: MessageBusProtocol | None, team_mgr: Any | None, message: MessageDict | None) -> bool:
    """Return whether a turn should poll teammate inbox replies."""

    return bool(bus and team_mgr and message is not None and not message.get("error") and message.get("tool_calls"))


def team_followup_timing(timeout_settings: Any | None, *, now: float) -> TeamFollowupTiming:
    """Return follow-up polling deadline and interval from runtime timeout settings."""

    lead_wait = timeout_settings.lead_wait if timeout_settings else 1800
    poll_interval = timeout_settings.lead_poll_interval if timeout_settings else 2
    return TeamFollowupTiming(deadline=now + lead_wait, poll_interval=poll_interval)


def inject_team_inbox_messages(messages: list[MessageDict], inbox_messages: list[dict[str, Any]], *, label: str = "兜底", timestamp: str | None = None) -> TeamInboxInjectionResult:
    """Inject teammate inbox replies into the conversation for a follow-up turn."""

    from ..team.loop import format_inbox_messages

    inbox_text = format_inbox_messages(inbox_messages)
    if not inbox_text:
        return TeamInboxInjectionResult(injected=False)

    ts = timestamp or now_ts()
    messages.append({"role": "user", "content": inbox_text, "timestamp": ts})
    messages.append({"role": "user", "content": TEAM_FOLLOWUP_INSTRUCTION, "timestamp": ts})
    return TeamInboxInjectionResult(injected=True, count=len(inbox_messages))
