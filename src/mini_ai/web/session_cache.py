"""Session cache eviction and cleanup helpers."""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from ..core.runtime_types import SessionComponents
from ..logger import logger


def cleanup_components(comp: SessionComponents) -> None:
    """Close closeable session-scoped components."""

    if not comp:
        return
    for name in ("compactor", "store"):
        try:
            obj = comp.get(name)
            if obj and hasattr(obj, "close"):
                obj.close()
        except Exception as exc:
            logger.warning(f"[Web] 关闭 {name} 失败: {exc}")


def evict_sessions(
    sessions: MutableMapping[str, Any],
    *,
    max_cached_sessions: int,
    keep_key: str | None = None,
) -> int:
    """Evict oldest inactive sessions from a session mapping."""

    candidates = [(key, state.access_time) for key, state in sessions.items()]
    candidates.sort(key=lambda item: item[1])

    evicted = 0
    target = len(sessions) - max_cached_sessions + 5
    for key, _ in candidates:
        if evicted >= target:
            break
        if key == keep_key:
            continue
        state = sessions.get(key)
        if not state:
            continue
        if state.refs > 0:
            continue
        if state.status == "generating":
            continue
        cleanup_components(state.components)
        del sessions[key]
        evicted += 1

    return evicted
