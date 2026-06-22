"""Web session persistence adapters."""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ..core.runtime_types import MessageDict, SessionComponents
from ..logger import logger


ResolveBase = Callable[[str, str | None], Path]
GetComponents = Callable[[str, str, Path | None, str | None], SessionComponents]


def load_messages_from_db(
    username: str,
    sid: str,
    base: Path | None = None,
    workspace: str | None = None,
    *,
    resolve_base: ResolveBase,
    get_components: GetComponents,
) -> list[MessageDict] | None:
    """Load session messages through the Web session component boundary."""

    started_at = time.time()
    try:
        if base is None:
            base = resolve_base(username, workspace or "default")
        comp = get_components(username, sid, base, workspace)
        settings = comp.get("settings")
        context_limit = settings.compactor.context_limit if settings else 50
        result = comp["history_db"].load_session(workspace or "default", sid, limit=context_limit)
        logger.debug(f"[perf] _load_from_db sid={sid} msgs={len(result) if result else 0} time={time.time()-started_at:.3f}s")
        return result
    except Exception as exc:
        logger.error(f"[Web] _load_from_db error: {exc}", exc_info=True)
        return None
