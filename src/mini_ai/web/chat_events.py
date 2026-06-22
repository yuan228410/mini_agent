"""Web chat queue event helpers.

Keep WebSocket route code focused on transport flow; event normalization and
terminal/usage bookkeeping live here as adapter-local policy.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..core.runtime_types import DisplayWireEvent
from ..logger import logger


TERMINAL_CHAT_EVENTS = {"done", "aborted", "error", "complete"}
EMPTY_USAGE = {"prompt_tokens": 0, "completion_tokens": 0}


@dataclass(frozen=True, slots=True)
class ChatQueueEvent:
    wire: dict[str, Any]
    usage: dict[str, int]
    terminal: bool


@dataclass(frozen=True, slots=True)
class ChatRelayResult:
    usage: dict[str, int]
    aborted: bool
    got_terminal: bool


SendChatEvent = Callable[[DisplayWireEvent], Awaitable[None]]


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


async def relay_chat_queue_events(
    *,
    queue: asyncio.Queue,
    abort_event,
    future: Any,
    session_id: str,
    send: SendChatEvent,
    poll_timeout: float = 0.15,
) -> ChatRelayResult:
    """Relay queued chat events until abort, terminal event, or runner completion."""

    usage = initial_chat_usage()
    aborted = False
    got_terminal = False
    while True:
        if abort_event.is_set():
            aborted = True
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=poll_timeout)
            queued = normalize_chat_queue_event(event, session_id=session_id, usage=usage)
            usage = queued.usage
            logger.info(f'[Web] WS dequeue: event={queued.wire["event"]} sid={session_id} has_error={bool(queued.wire["data"].get("error"))}')
            if queued.terminal:
                logger.debug(f'[Web] terminal event from queue sid={session_id} event={queued.wire["event"]}')
            await send(queued.wire)
            logger.info(f'[Web] WS after _send: event={queued.wire["event"]} sid={session_id}')
            if queued.terminal:
                got_terminal = True
                logger.info(f'[Web] WS breaking: event={queued.wire["event"]} sid={session_id}')
                break
        except asyncio.TimeoutError:
            if future.done():
                for queued in drain_ready_chat_events(queue, session_id=session_id, usage=usage):
                    usage = queued.usage
                    if queued.terminal:
                        logger.debug(f'[Web] drained terminal event sid={session_id} event={queued.wire["event"]}')
                    await send(queued.wire)
                    logger.info(f'[Web] WS drain: event={queued.wire["event"]} sid={session_id}')
                    if queued.terminal:
                        got_terminal = True
                        break
                break
    return ChatRelayResult(usage=usage, aborted=aborted, got_terminal=got_terminal)
