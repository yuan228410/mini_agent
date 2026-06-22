"""WebSocket chat run finalization helpers."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..core.events import DisplayEventType
from ..core.runtime_types import DisplayWireEvent
from ..logger import logger
from .chat_command_dispatch import error_event, ws_event
from .chat_run_context import ChatRunContext

SendChatEvent = Callable[[DisplayWireEvent], Awaitable[None]]


async def finalize_chat_run(
    *,
    run_context: ChatRunContext,
    future: Any,
    session_manager: Any,
    update_meta_cache: Callable[[str, str, str | None, list[dict[str, Any]] | None], None],
    send: SendChatEvent,
    sid: str,
    username: str,
    workspace: str | None,
    usage: dict[str, int],
    aborted: bool,
    got_terminal: bool,
) -> None:
    """Finalize usage/meta/task state after a WebSocket chat run."""

    if aborted:
        logger.info(f"[Web] chat aborted sid={sid}")
        await send(ws_event(DisplayEventType.ABORTED, session_id=sid))
        try:
            future.cancel()
        except Exception:
            pass

    session_manager.set_last_usage(run_context.session_key, usage)
    update_meta_cache(username, sid, workspace, run_context.messages)

    logger.info(f"[Web] WS loop exit: aborted={aborted} got_terminal={got_terminal} sid={sid}")
    if not aborted and not got_terminal:
        try:
            future.result()
        except Exception as exc:
            logger.error(f"[Web] chat runner ended without terminal event: {exc}", exc_info=True)
            await send(error_event(str(exc), sid))
        else:
            logger.debug(f"[Web] sending done sid={sid} usage={usage}")
            await send(ws_event(
                DisplayEventType.DONE,
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                session_id=sid,
            ))

    session_manager.release_active_task(run_context.session_key, asyncio.current_task())
