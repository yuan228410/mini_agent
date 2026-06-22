"""Web chat run-context assembly helpers."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..application import chat_service
from ..core.runtime_types import MessageDict
from .route_types import ImageUpload


@dataclass(frozen=True, slots=True)
class ChatRunContext:
    session_key: str
    base: Path
    messages: list[MessageDict]
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    abort_event: threading.Event
    model_name: str | None
    session_lock: Any
    max_turns: int

    def executor_args(
        self,
        *,
        username: str,
        workspace: str | None,
        plan_turn: bool,
        approved_plan: dict | None,
    ) -> tuple[Any, ...]:
        """Return positional args for the synchronous chat runner."""

        return (
            self.queue,
            self.loop,
            self.messages,
            None,
            self.max_turns,
            self.abort_event,
            self.model_name,
            self.session_lock,
            self.session_key,
            username,
            workspace,
            plan_turn,
            approved_plan,
        )


def prepared_abort_event(session_manager: Any, session_key: str) -> threading.Event:
    """Return a clean abort event for a new chat run."""

    abort_event = session_manager.get_abort_event(session_key)
    if abort_event is None:
        return threading.Event()
    abort_event.clear()
    return abort_event


def prepare_chat_run_context(
    deps: dict[str, Any],
    *,
    username: str,
    session_id: str,
    workspace: str | None,
    user_message: str,
    images: list[ImageUpload] | None,
) -> ChatRunContext:
    """Prepare session, queue, abort and model state for one WebSocket chat run."""

    session_manager = deps["session_manager"]
    session_key = deps["cache_key"](username, workspace, session_id)
    base = deps["resolve_base"](username, workspace)
    messages = deps["get_or_create_session"](username, session_id, base, workspace)[1]
    chat_service.append_user_message(messages, user_message, images)

    components = deps["get_or_create_components"](username, session_id, base, workspace)
    settings = components.get("settings")
    return ChatRunContext(
        session_key=session_key,
        base=base,
        messages=messages,
        queue=asyncio.Queue(maxsize=1000),
        loop=asyncio.get_event_loop(),
        abort_event=prepared_abort_event(session_manager, session_key),
        model_name=session_manager.get_model(session_key),
        session_lock=session_manager.get_lock(session_key),
        max_turns=settings.web.max_turns if settings else 10,
    )
