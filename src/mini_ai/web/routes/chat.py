"""聊天接口 — WebSocket 模式 + 聊天历史/导出/重置

从原 1446 行拆分后，本文件只保留：
- WebSocket endpoint (chat_ws_endpoint)
- _run_chat / _reader 协程
- REST: chat_history, chat_export, chat_reset
"""
import asyncio

from fastapi import APIRouter, Query, WebSocket

from ...application import chat_service
from ...core.runtime_types import PlanArtifactDict
from ...logger import logger
from ..route_types import (
    ChatHistoryResponse,
    ChatResetRequest,
    ChatResetResponse,
    ImageUpload,
    RouteErrorResponse,
)
from ..chat_command_dispatch import error_event
from ..chat_connection_cleanup import cleanup_chat_connection
from ..chat_connection_driver import drive_chat_connection
from ..chat_endpoint_state import build_chat_endpoint_state
from ..chat_events import relay_chat_queue_events
from ..chat_executor import launch_chat_executor
from ..chat_export_response import chat_export_response
from ..chat_run_context import prepare_chat_run_context
from ..chat_run_finalization import finalize_chat_run
from ..chat_task_launcher import chat_task_launcher
from ..runtime_helpers import chat_rest_dependencies

router = APIRouter()


# ── WebSocket endpoint ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()
    endpoint_state = build_chat_endpoint_state(ws)
    deps = endpoint_state.session_dependencies
    sm = endpoint_state.session_manager

    async def _send(data) -> None:
        await endpoint_state.send(data)

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
        launcher = chat_task_launcher(session_manager=sm, cache_key=deps["cache_key"], send=_send, run_chat=_run_chat)
        await launcher.launch(sid, username, user_message, ws_name, images, plan_turn, approved_plan, session_key)

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None, images: list[ImageUpload] | None = None, plan_turn: bool = False, approved_plan: PlanArtifactDict | None = None) -> None:
        logger.info(f"[Web] WS _run_chat sid={sid} user={username} ws={ws_name} images={len(images) if images else 0} plan_turn={plan_turn} approved={bool(approved_plan)}")
        run_context = prepare_chat_run_context(
            deps,
            username=username,
            session_id=sid,
            workspace=ws_name,
            user_message=user_message,
            images=images,
        )
        endpoint_state.abort_keys.append(run_context.session_key)

        future = launch_chat_executor(
            run_context,
            username=username,
            workspace=ws_name,
            plan_turn=plan_turn,
            approved_plan=approved_plan,
        )

        try:
            relay_result = await relay_chat_queue_events(
                queue=run_context.queue,
                abort_event=run_context.abort_event,
                future=future,
                session_id=sid,
                send=_send,
            )
        except Exception as e:
            logger.error(f"[Web] WS chat task error: {e}", exc_info=True)
            await _send(error_event(str(e), sid))
            relay_result = None

        await finalize_chat_run(
            run_context=run_context,
            future=future,
            session_manager=sm,
            update_meta_cache=endpoint_state.update_meta_cache,
            send=_send,
            sid=sid,
            username=username,
            workspace=ws_name,
            usage=relay_result.usage if relay_result else {"prompt_tokens": 0, "completion_tokens": 0},
            aborted=relay_result.aborted if relay_result else False,
            got_terminal=relay_result.got_terminal if relay_result else False,
        )

    async def _cleanup_reader(reader_task: asyncio.Task) -> None:
        await cleanup_chat_connection(reader_task=reader_task, abort_keys=endpoint_state.abort_keys, session_manager=sm)

    await drive_chat_connection(
        receive_text=ws.receive_text,
        command_deps=endpoint_state.command_dependencies,
        send=_send,
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
