"""WebSocket chat endpoint adapter."""
from __future__ import annotations

import asyncio

from fastapi import WebSocket

from ..core.runtime_types import PlanArtifactDict
from .chat_connection_cleanup import cleanup_chat_connection
from .chat_connection_driver import drive_chat_connection
from .chat_endpoint_state import build_chat_endpoint_state
from .chat_run_coordinator import run_chat_websocket_turn
from .chat_task_launcher import chat_task_launcher
from .route_types import ImageUpload


async def handle_chat_websocket(websocket: WebSocket) -> None:
    """Accept and drive one chat WebSocket connection."""

    await websocket.accept()
    endpoint_state = build_chat_endpoint_state(websocket)

    async def _run_chat(
        sid: str,
        username: str,
        user_message: str,
        ws_name: str | None = None,
        images: list[ImageUpload] | None = None,
        plan_turn: bool = False,
        approved_plan: PlanArtifactDict | None = None,
    ) -> None:
        await run_chat_websocket_turn(
            endpoint_state,
            sid=sid,
            username=username,
            user_message=user_message,
            workspace=ws_name,
            images=images,
            plan_turn=plan_turn,
            approved_plan=approved_plan,
        )

    async def _launch_chat_task(
        sid: str,
        username: str,
        user_message: str,
        ws_name: str | None = None,
        images: list[ImageUpload] | None = None,
        plan_turn: bool = False,
        approved_plan: PlanArtifactDict | None = None,
        session_key: str | None = None,
    ) -> None:
        launcher = chat_task_launcher(
            session_manager=endpoint_state.session_manager,
            cache_key=endpoint_state.cache_key,
            send=endpoint_state.send,
            run_chat=_run_chat,
        )
        await launcher.launch(sid, username, user_message, ws_name, images, plan_turn, approved_plan, session_key)

    async def _cleanup_reader(reader_task: asyncio.Task) -> None:
        await cleanup_chat_connection(
            reader_task=reader_task,
            abort_keys=endpoint_state.abort_keys,
            session_manager=endpoint_state.session_manager,
        )

    await drive_chat_connection(
        receive_text=websocket.receive_text,
        command_deps=endpoint_state.command_dependencies,
        send=endpoint_state.send,
        launch_chat_task=_launch_chat_task,
        cleanup_reader=_cleanup_reader,
    )
