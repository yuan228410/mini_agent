"""WebSocket chat endpoint state assembly."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import WebSocket

from ..core.events import DisplayEvent
from ..core.runtime_types import DisplayWireEvent
from .chat_command_dispatch import ChatCommandDependencies
from .chat_sender import ChatWebSocketSender
from .chat_dependencies import chat_compact_dependencies, chat_session_dependencies, plan_command_dependencies


@dataclass(slots=True)
class ChatEndpointState:
    """Per-connection state and dependencies for the chat WebSocket endpoint."""

    session_dependencies: dict[str, Any]
    command_dependencies: ChatCommandDependencies
    sender: ChatWebSocketSender
    abort_keys: list[str] = field(default_factory=list)

    @property
    def session_manager(self):
        return self.session_dependencies["session_manager"]

    @property
    def cache_key(self):
        return self.session_dependencies["cache_key"]

    @property
    def update_meta_cache(self) -> Callable[[str, str, str | None, list[dict[str, Any]] | None], None]:
        return self.session_dependencies["update_meta_cache"]

    async def send(self, data: DisplayWireEvent | DisplayEvent) -> None:
        await self.sender.send(data)


def build_chat_endpoint_state(websocket: WebSocket) -> ChatEndpointState:
    """Build all WebSocket chat endpoint dependencies at the adapter boundary."""

    session_deps = chat_session_dependencies()
    command_deps = ChatCommandDependencies(
        session_manager=session_deps["session_manager"],
        cache_key=session_deps["cache_key"],
        plan_dependencies=plan_command_dependencies(),
        compact_dependencies=chat_compact_dependencies(),
    )
    return ChatEndpointState(
        session_dependencies=session_deps,
        command_dependencies=command_deps,
        sender=ChatWebSocketSender(websocket),
    )
