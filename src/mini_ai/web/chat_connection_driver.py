"""WebSocket chat connection lifecycle driver."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..logger import logger
from .chat_command_dispatch import ChatCommandDependencies, LaunchChatTask, SendChatEvent, dispatch_chat_ws_message

ReceiveText = Callable[[], Awaitable[str]]
CleanupReader = Callable[[asyncio.Task], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChatConnectionState:
    username: str | None = None


async def run_chat_reader_loop(
    *,
    receive_text: ReceiveText,
    username: str | None,
    command_deps: ChatCommandDependencies,
    send: SendChatEvent,
    launch_chat_task: LaunchChatTask,
    timeout: float = 30.0,
) -> ChatConnectionState:
    """Read WebSocket messages and dispatch chat commands until the connection closes."""

    current_username = username
    try:
        while True:
            try:
                raw = await asyncio.wait_for(receive_text(), timeout=timeout)
                state = await dispatch_chat_ws_message(
                    raw,
                    username=current_username,
                    deps=command_deps,
                    send=send,
                    launch_chat_task=launch_chat_task,
                )
                current_username = state.username
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                logger.debug(f"[Web] WS receive error: {exc}")
                break
    except Exception as exc:
        logger.debug(f"[Web] WS reader 退出: {exc}")
    return ChatConnectionState(username=current_username)


async def drive_chat_connection(
    *,
    receive_text: ReceiveText,
    command_deps: ChatCommandDependencies,
    send: SendChatEvent,
    launch_chat_task: LaunchChatTask,
    cleanup_reader: CleanupReader,
    poll_interval: float = 0.5,
) -> ChatConnectionState:
    """Run the reader task and cleanup hooks for one WebSocket chat connection."""

    reader_result: ChatConnectionState | None = None

    async def _reader() -> None:
        nonlocal reader_result
        reader_result = await run_chat_reader_loop(
            receive_text=receive_text,
            username=None,
            command_deps=command_deps,
            send=send,
            launch_chat_task=launch_chat_task,
        )

    reader_task = asyncio.create_task(_reader())
    try:
        while not reader_task.done():
            await asyncio.sleep(poll_interval)
    except Exception:
        pass
    finally:
        await cleanup_reader(reader_task)
    return reader_result or ChatConnectionState()
