"""Application-level chat compaction command handling."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.events import DisplayEvent, DisplayEventType
from ..core.runtime_types import DisplayWireEvent, MessageDict, RequestContextProtocol


@dataclass(frozen=True, slots=True)
class ChatCompactDependencies:
    session_manager: Any
    cache_key: Callable[[str, str | None, str], str]
    resolve_base: Callable[[str, str | None], Path]
    get_or_create_session: Callable[..., tuple[str, list[MessageDict] | None]]
    get_or_create_components: Callable[[str, str, Path | None, str | None], dict[str, Any]]
    settings_for_model: Callable[[Any, str | None], Any]
    request_context_for_settings: Callable[..., RequestContextProtocol]
    chat: Callable[..., MessageDict]


@dataclass(frozen=True, slots=True)
class ChatCompactResult:
    event: DisplayWireEvent
    sid: str | None = None


def _info_event(message: str, session_id: str | None = None) -> DisplayWireEvent:
    payload: dict[str, Any] = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    return DisplayEvent(DisplayEventType.INFO, payload).to_wire()


def _error_event(error: str, session_id: str | None = None) -> DisplayWireEvent:
    payload: dict[str, Any] = {"error": error}
    if session_id:
        payload["session_id"] = session_id
    return DisplayEvent(DisplayEventType.ERROR, payload).to_wire()


def compact_chat(deps: ChatCompactDependencies, *, username: str, session_id: str | None, workspace: str | None) -> ChatCompactResult:
    """Compact a chat session and return the adapter-ready status event."""

    if not session_id:
        return ChatCompactResult(_error_event("请先选择会话"))

    sid, messages = deps.get_or_create_session(username, session_id, workspace=workspace, create=False)
    if messages is None:
        return ChatCompactResult(_error_event(f"会话 {session_id} 不存在"), sid=sid)

    base = deps.resolve_base(username, workspace)
    comp = deps.get_or_create_components(username, sid, base, workspace)
    compactor = comp["compactor"]
    non_system = [m for m in messages if m["role"] != "system"]

    if len(non_system) <= compactor.keep_recent:
        return ChatCompactResult(
            _info_event(f"消息数({len(non_system)})未超过保留阈值({compactor.keep_recent})，无需压缩", sid),
            sid=sid,
        )

    before = len(non_system)
    session_key = deps.cache_key(username, workspace, sid)
    model_name = deps.session_manager.get_model(session_key)
    settings = deps.settings_for_model(comp["settings"], model_name)
    ctx = deps.request_context_for_settings(settings, display=None)

    try:
        messages[:] = compactor.compact(deps.chat, messages, ctx=ctx)
        after = len([m for m in messages if m["role"] != "system"])
        return ChatCompactResult(_info_event(f"✅ 压缩完成：{before} → {after} 条消息", sid), sid=sid)
    except Exception as exc:
        return ChatCompactResult(_error_event(f"压缩失败: {exc}", sid), sid=sid)
    finally:
        ctx.close()
