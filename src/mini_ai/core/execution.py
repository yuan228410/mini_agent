"""Execution-control primitives shared by runner, tools and adapters."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    """Resource limits for one runtime/session.

    These are intentionally conservative defaults.  SettingsSnapshot is the
    source of truth; this DTO lets lower layers avoid importing config globals.
    """

    max_parallel_tools: int = 8
    max_web_turns: int = 10
    max_workflow_concurrency: int = 8
    max_subagents: int = 4
    stream_chunk_flush_ms: int = 40
    stream_chunk_max_chars: int = 512


class CancellationToken:
    """Small wrapper around threading.Event for explicit cancellation flow."""

    def __init__(self, event: Event | None = None) -> None:
        self._event = event or Event()

    @property
    def event(self) -> Event:
        return self._event

    def cancel(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout=timeout)
