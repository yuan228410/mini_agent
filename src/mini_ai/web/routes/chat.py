"""Thin chat route adapters for WebSocket chat and chat REST endpoints."""
import asyncio

from fastapi import APIRouter, Query, WebSocket

from ...application import chat_service
from ...core.runtime_types import PlanArtifactDict
from ..route_types import (
    ChatHistoryResponse,
    ChatResetRequest,
    ChatResetResponse,
    ImageUpload,
    RouteErrorResponse,
)
from ..chat_connection_cleanup import cleanup_chat_connection
from ..chat_connection_driver import drive_chat_connection
from ..chat_endpoint_state import build_chat_endpoint_state
from ..chat_export_response import chat_export_response
from ..chat_run_coordinator import run_chat_websocket_turn
from ..chat_task_launcher import chat_task_launcher
from ..runtime_helpers import chat_rest_dependencies

router = APIRouter()


# ── WebSocket endpoint ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()
    endpoint_state = build_chat_endpoint_state(ws)
    sm = endpoint_state.session_manager

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
        launcher = chat_task_launcher(session_manager=sm, cache_key=endpoint_state.cache_key, send=endpoint_state.send, run_chat=_run_chat)
        await launcher.launch(sid, username, user_message, ws_name, images, plan_turn, approved_plan, session_key)

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None, images: list[ImageUpload] | None = None, plan_turn: bool = False, approved_plan: PlanArtifactDict | None = None) -> None:
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

    async def _cleanup_reader(reader_task: asyncio.Task) -> None:
        await cleanup_chat_connection(reader_task=reader_task, abort_keys=endpoint_state.abort_keys, session_manager=sm)

    await drive_chat_connection(
        receive_text=ws.receive_text,
        command_deps=endpoint_state.command_dependencies,
        send=endpoint_state.send,
        launch_chat_task=_launch_chat_task,
        cleanup_reader=_cleanup_reader,
    )


# ── REST: 聊天历史 ──

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")) -> ChatHistoryResponse:
    return chat_service.chat_history(chat_rest_dependencies(), session_id=session_id, username=username, workspace=workspace)


# ── REST: 重置会话 ──

@router.post("/chat/reset")
async def chat_reset(body: ChatResetRequest | None = None) -> ChatResetResponse | RouteErrorResponse:
    return chat_service.reset_chat(chat_rest_dependencies(), body)


# ── REST: 导出会话 ──

@router.get("/chat/export")
async def chat_export(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default=""), limit: int = Query(default=0), include_thinking: bool = Query(default=False), include_tools: bool = Query(default=False)):
    result = chat_service.export_chat(
        chat_rest_dependencies(),
        session_id=session_id,
        username=username,
        workspace=workspace,
        limit=limit,
        include_thinking=include_thinking,
        include_tools=include_tools,
    )
    return chat_export_response(result)
