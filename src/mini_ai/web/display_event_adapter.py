"""Web display event-to-wire adapter."""
from __future__ import annotations

import threading
from collections.abc import Callable

from ..core.events import DisplayEventType, TERMINAL_EVENT_TYPES
from ..core.runtime_types import DisplayEventPayload, DisplayWireEvent
from ..logger import logger

_EVENT_SEQS: dict[str, int] = {}
_EVENT_SEQS_LOCK = threading.Lock()
TERMINAL_EVENT_NAMES = {event.value for event in TERMINAL_EVENT_TYPES}
USAGE_EVENT_NAMES = TERMINAL_EVENT_NAMES | {DisplayEventType.LLM_ROUND_END.value, DisplayEventType.DONE.value}


UsageProvider = Callable[[], object]


def cleanup_session_seq(session_id: str) -> None:
    """清理会话事件序号，避免长生命周期 Web 服务累积。"""

    if not session_id:
        return
    with _EVENT_SEQS_LOCK:
        _EVENT_SEQS.pop(session_id, None)


class WebDisplayEventAdapter:
    """Convert display events into WebSocket wire payloads."""

    def __init__(self, *, session_id: str = "", agent_id: str = "", usage_provider: UsageProvider | None = None):
        self.session_id = session_id
        self.agent_id = agent_id
        self._usage_provider = usage_provider

    def child(self, *, agent_id: str = "") -> "WebDisplayEventAdapter":
        """Create a child adapter sharing session and usage boundaries."""

        return WebDisplayEventAdapter(
            session_id=self.session_id,
            agent_id=agent_id or self.agent_id,
            usage_provider=self._usage_provider,
        )

    def set_agent_id(self, agent_id: str) -> None:
        self.agent_id = agent_id

    def set_usage_provider(self, usage_provider: UsageProvider | None) -> None:
        self._usage_provider = usage_provider

    @property
    def usage_provider(self) -> UsageProvider | None:
        return self._usage_provider

    def to_wire(self, event: str | DisplayEventType, data: DisplayEventPayload | None = None) -> DisplayWireEvent:
        """Return a WebSocket wire event with Web-only metadata injected."""

        event_name = event.value if isinstance(event, DisplayEventType) else event
        payload: DisplayEventPayload = dict(data or {})
        usage = self._usage_snapshot() if event_name in USAGE_EVENT_NAMES else {}

        if event_name in TERMINAL_EVENT_NAMES:
            logger.info(
                f'[Display] push: event={event_name} sid={self.session_id} '
                f'has_err={bool(payload and payload.get("error"))}'
            )
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.agent_id:
            payload["agent_id"] = self.agent_id
        if usage:
            payload.setdefault("prompt_tokens", usage.get("prompt_tokens", 0))
            payload.setdefault("completion_tokens", usage.get("completion_tokens", 0))
        if self.session_id:
            payload["seq"] = self._next_seq()
        return {"event": event_name, "data": payload}

    def _usage_snapshot(self) -> dict:
        if self._usage_provider is None:
            return {"prompt_tokens": 0, "completion_tokens": 0}
        usage = self._usage_provider()
        if hasattr(usage, "to_dict"):
            usage = usage.to_dict()
        return dict(usage or {})

    def _next_seq(self) -> int:
        with _EVENT_SEQS_LOCK:
            seq = _EVENT_SEQS.get(self.session_id, 0) + 1
            _EVENT_SEQS[self.session_id] = seq
            return seq
