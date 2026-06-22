"""Chat history response helpers for application services."""
from __future__ import annotations

from typing import Any

from ..core.runtime_types import MessageDict


def chat_history(deps: Any, *, session_id: str = "", username: str, workspace: str = "") -> dict[str, Any]:
    """Return display-ready chat history for a Web session."""

    if not username:
        return {"session_id": session_id, "history": []}
    if not session_id:
        return {"session_id": "", "history": []}
    try:
        base = deps.resolve_base(username, workspace or None)
    except Exception:
        return {"session_id": session_id, "history": []}

    key = deps.cache_key(username, workspace or None, session_id)
    mem_msgs = deps.session_manager.get_messages(key)

    if mem_msgs:
        messages = [m for m in mem_msgs if m["role"] not in ("system", "tool")]
        comp = deps.get_or_create_components(username, session_id, base, workspace or None)
        current_plan = comp["history_db"].get_current_plan(workspace or "default", session_id)
    else:
        comp = deps.get_or_create_components(username, session_id, base, workspace or None)
        messages = comp["history_db"].load_session_for_display(workspace or "default", session_id) or []
        current_plan = comp["history_db"].get_current_plan(workspace or "default", session_id)

    return {"session_id": session_id, "history": history_entries(messages), "current_plan": current_plan}


def history_entries(messages: list[MessageDict]) -> list[dict[str, Any]]:
    """Convert persisted/runtime messages into Web history entries."""

    return [_history_entry(message) for message in messages]


def _history_entry(message: MessageDict) -> dict[str, Any]:
    entry: dict[str, Any] = {"role": message["role"]}
    content = message.get("content")

    if isinstance(content, list):
        text_parts = []
        images: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    img_url = part.get("image_url", {}).get("url", "")
                    if img_url:
                        images.append({"dataUrl": img_url, "name": "", "size": 0})
        entry["content"] = "\n".join(text_parts)
        if images:
            entry["images"] = images
    elif content:
        entry["content"] = content

    for key_name in ("timestamp", "thinking", "tool_calls", "kind", "plan"):
        if message.get(key_name):
            entry[key_name] = message[key_name]
    return entry
