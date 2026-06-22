"""WebSocket chat task launch helpers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..core.runtime_types import PlanArtifactDict
from .chat_command_dispatch import SendChatEvent, error_event
from .route_types import ImageUpload

RunChat = Callable[[str, str, str, str | None, list[ImageUpload] | None, bool, PlanArtifactDict | None], Awaitable[None]]
CacheKey = Callable[[str, str | None, str], str]


@dataclass(frozen=True, slots=True)
class ChatTaskLauncher:
    """Create chat tasks and claim the active-session slot."""

    session_manager: object
    cache_key: CacheKey
    send: SendChatEvent
    run_chat: RunChat

    async def launch(
        self,
        sid: str,
        username: str,
        user_message: str,
        ws_name: str | None = None,
        images: list[ImageUpload] | None = None,
        plan_turn: bool = False,
        approved_plan: PlanArtifactDict | None = None,
        session_key: str | None = None,
    ) -> None:
        task = asyncio.create_task(self.run_chat(sid, username, user_message, ws_name, images, plan_turn, approved_plan))
        claim_active_task = getattr(self.session_manager, "claim_active_task")
        if not claim_active_task(session_key or self.cache_key(username, ws_name, sid), task):
            task.cancel()
            await self.send(error_event("当前会话正在生成，请稍后再发送", sid))


def chat_task_launcher(*, session_manager: object, cache_key: CacheKey, send: SendChatEvent, run_chat: RunChat) -> ChatTaskLauncher:
    """Build a chat task launcher for one WebSocket connection."""

    return ChatTaskLauncher(session_manager=session_manager, cache_key=cache_key, send=send, run_chat=run_chat)
