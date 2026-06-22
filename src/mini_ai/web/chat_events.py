"""Web chat queue event helpers.

Keep WebSocket route code focused on transport flow; event normalization and
terminal/usage bookkeeping live here as adapter-local policy.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


TERMINAL_CHAT_EVENTS = {"done", "aborted", "error", "complete"}
EMPTY_USAGE = {"prompt_tokens": 0, "completion_tokens": 0}


@dataclass(frozen=True, slots=True)
class ChatQueueEvent:
    wire: dict[str, Any]
    usage: dict[str, int]
    terminal: bool


def initial_chat_usage() -> dict[str, int]:
    """Return a fresh usage accumulator for one WebSocket chat run."""

    return dict(EMPTY_USAGE)


def normalize_chat_queue_event(event: dict[str, Any], *, session_id: str, usage: dict[str, int]) -> ChatQueueEvent:
    """Attach session metadata and update usage/terminal state for a queue event."""

    data = event.setdefault("data", {})
    data["session_id"] = session_id
    next_usage = usage
    if event.get("event") == "complete" and "prompt_tokens" in data:
        next_usage = {
            "prompt_tokens": int(data.get("prompt_tokens") or 0),
            "completion_tokens": int(data.get("completion_tokens") or 0),
        }
    return ChatQueueEvent(wire=event, usage=next_usage, terminal=event.get("event") in TERMINAL_CHAT_EVENTS)


def drain_ready_chat_events(
    queue: asyncio.Queue,
    *,
    session_id: str,
    usage: dict[str, int],
    limit: int = 10,
) -> list[ChatQueueEvent]:
    """Drain immediately available queue events, stopping after a terminal event."""

    events: list[ChatQueueEvent] = []
    current_usage = usage
    for _ in range(limit):
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        normalized = normalize_chat_queue_event(event, session_id=session_id, usage=current_usage)
        events.append(normalized)
        current_usage = normalized.usage
        if normalized.terminal:
            break
    return events
