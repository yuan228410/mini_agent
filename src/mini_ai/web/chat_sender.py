"""WebSocket chat send helpers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from fastapi import WebSocket

from ..core.events import DisplayEvent
from ..core.runtime_types import DisplayWireEvent
from ..logger import logger


@dataclass(slots=True)
class ChatWebSocketSender:
    """Serialize WebSocket sends and normalize DisplayEvent objects."""

    websocket: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, data: DisplayWireEvent | DisplayEvent) -> None:
        wire = data.to_wire() if isinstance(data, DisplayEvent) else data
        async with self.lock:
            try:
                await self.websocket.send_json(wire)
            except Exception as exc:
                logger.warning(f'[Web] _send failed: {exc}, event={wire.get("event")}')


async def send_chat_event(sender: ChatWebSocketSender, data: DisplayWireEvent | DisplayEvent) -> None:
    """Send one WebSocket chat event through the locked sender."""

    await sender.send(data)
