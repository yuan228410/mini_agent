"""Chat history export helpers for application services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChatExportResult:
    content: str
    filename: str


def export_chat(
    deps: Any,
    *,
    session_id: str = "",
    username: str,
    workspace: str = "",
    limit: int = 0,
    include_thinking: bool = False,
    include_tools: bool = False,
) -> ChatExportResult | dict[str, Any]:
    """Build a markdown export for a chat session."""

    if not username:
        return {"error": "缺少 username", "status_code": 400}
    if not session_id:
        return {"error": "缺少 session_id", "status_code": 400}
    try:
        base = deps.resolve_base(username, workspace or None)
    except Exception as exc:
        return {"error": f"工作空间错误: {exc}", "status_code": 400}

    comp = deps.get_or_create_components(username, session_id, base, workspace or None)
    messages = comp["history_db"].load_session(workspace or "default", session_id, limit=limit) or []
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在或无消息", "status_code": 404}

    session_name = _session_export_name(deps, base, session_id, messages)
    lines = [f"# {session_name}\n"]
    for message in messages:
        lines.extend(_message_export_lines(message, include_thinking=include_thinking, include_tools=include_tools))

    safe_name = session_name.replace("/", "-").replace(" ", "-")[:60]
    return ChatExportResult(content="\n".join(lines), filename=safe_name)


def _session_export_name(deps: Any, base, session_id: str, messages: list[dict[str, Any]]) -> str:
    session_name = deps.load_session_name(base, session_id)
    if not session_name:
        for message in messages:
            if message.get("role") == "user" and message.get("content"):
                session_name = str(message["content"])[:50]
                break
    return session_name or session_id


def _message_export_lines(message: dict[str, Any], *, include_thinking: bool, include_tools: bool) -> list[str]:
    role = message.get("role", "")
    content = message.get("content") or ""
    ts = message.get("timestamp", "")
    if role in ("system", "tool"):
        return []
    if role == "user":
        label = "**🧑 用户**"
        if ts:
            label += f"  `{ts}`"
        return [f"\n{label}\n\n{content}\n"]
    if role != "assistant":
        return []

    thinking = message.get("thinking")
    tool_calls = message.get("tool_calls")
    has_thinking = include_thinking and thinking
    has_tools = include_tools and tool_calls
    if not content and not has_thinking and not has_tools:
        return []

    label = "**🤖 助手**"
    if ts:
        label += f"  `{ts}`"
    lines = [f"\n{label}\n"]
    if has_thinking:
        thinking_text = thinking if isinstance(thinking, str) else str(thinking)
        lines.append(f"\n<details>\n<summary>💭 思考过程</summary>\n\n{thinking_text}\n\n</details>\n")
    if has_tools:
        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            name = fn.get("name", "?")
            args = str(fn.get("arguments", ""))
            result = tool_call.get("_result", "")
            lines.append(f"\n> 🔧 **{name}**({args[:200]})\n")
            if result:
                lines.append(f"> 结果: {result[:500]}\n")
    if content:
        lines.append(f"\n{content}\n")
    return lines
