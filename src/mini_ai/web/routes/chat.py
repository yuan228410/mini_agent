"""Thin chat route adapters for WebSocket chat and chat REST endpoints."""
from fastapi import APIRouter, Query, WebSocket

from ..route_types import ChatHistoryResponse, ChatResetRequest, ChatResetResponse, RouteErrorResponse
from ..chat_rest_adapter import chat_export_route_response, chat_history_response, chat_reset_response
from ..chat_websocket_endpoint import handle_chat_websocket

router = APIRouter()


# ── WebSocket endpoint ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await handle_chat_websocket(ws)


# ── REST: 聊天历史 ──

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")) -> ChatHistoryResponse:
    return chat_history_response(session_id=session_id, username=username, workspace=workspace)


# ── REST: 重置会话 ──

@router.post("/chat/reset")
async def chat_reset(body: ChatResetRequest | None = None) -> ChatResetResponse | RouteErrorResponse:
    return chat_reset_response(body)


# ── REST: 导出会话 ──

@router.get("/chat/export")
async def chat_export(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default=""), limit: int = Query(default=0), include_thinking: bool = Query(default=False), include_tools: bool = Query(default=False)):
    return chat_export_route_response(
        session_id=session_id,
        username=username,
        workspace=workspace,
        limit=limit,
        include_thinking=include_thinking,
        include_tools=include_tools,
    )
