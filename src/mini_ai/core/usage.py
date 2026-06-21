"""Session-local usage accounting primitives.

Core code should prefer this collector over process-global usage counters.  UI
adapters can read snapshots from the runtime without knowing which provider
produced the usage values.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from .runtime_types import UsageDict


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> UsageDict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class UsageCollector:
    """Thread-safe per-session usage accumulator."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._prompt_tokens = 0
        self._completion_tokens = 0

    def reset(self) -> None:
        with self._lock:
            self._prompt_tokens = 0
            self._completion_tokens = 0

    def add(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> UsageSnapshot:
        with self._lock:
            self._prompt_tokens += int(prompt_tokens or 0)
            self._completion_tokens += int(completion_tokens or 0)
            return self.snapshot_unlocked()

    def set(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> UsageSnapshot:
        with self._lock:
            self._prompt_tokens = int(prompt_tokens or 0)
            self._completion_tokens = int(completion_tokens or 0)
            return self.snapshot_unlocked()

    def snapshot_unlocked(self) -> UsageSnapshot:
        total = self._prompt_tokens + self._completion_tokens
        return UsageSnapshot(
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=total,
        )

    def snapshot(self) -> UsageSnapshot:
        with self._lock:
            return self.snapshot_unlocked()
