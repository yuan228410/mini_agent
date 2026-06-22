"""WebSocket chat connection cleanup helpers."""
from __future__ import annotations

import asyncio
from typing import Any


async def cleanup_chat_connection(*, reader_task: asyncio.Task, abort_keys: list[str], session_manager: Any) -> None:
    """Abort active sessions and stop the reader task for a closing WebSocket."""

    for key in abort_keys:
        evt = session_manager.get_abort_event(key)
        if evt:
            evt.set()
    reader_task.cancel()
    try:
        await reader_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
