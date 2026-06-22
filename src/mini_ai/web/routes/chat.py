"""聊天接口 — WebSocket 模式 + 聊天历史/导出/重置

从原 1446 行拆分后，本文件只保留：
- WebSocket endpoint (chat_ws_endpoint)
- _run_chat / _reader 协程
- REST: chat_history, chat_export, chat_reset
"""
import asyncio

from fastapi import APIRouter, Query, WebSocket

from ...application import chat_service
from ...core.events import DisplayEvent
from ...core.runtime_types import DisplayWireEvent, PlanArtifactDict
from ...logger import logger
from ..route_types import (
    ChatHistoryResponse,
    ChatResetRequest,
    ChatResetResponse,
    ImageUpload,
    RouteErrorResponse,
)
from ..chat_command_dispatch import ChatCommandDependencies, dispatch_chat_ws_message, error_event, ws_event
from ..chat_events import drain_ready_chat_events, initial_chat_usage, normalize_chat_queue_event
from ..chat_run_context import prepare_chat_run_context
from ..chat_run_finalization import finalize_chat_run
from ..chat_runner import run_tool_loop_sync
from ..runtime_helpers import chat_compact_dependencies, chat_rest_dependencies, chat_session_dependencies, plan_command_dependencies

router = APIRouter()


def _chat_deps():
    return chat_session_dependencies()


# ── WebSocket endpoint ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_abort_keys: list[str] = []
    ws_closed = False
    _write_lock = asyncio.Lock()
    _ws_username: str | None = None
    deps = _chat_deps()
    plan_deps = plan_command_dependencies()
    compact_deps = chat_compact_dependencies()
    command_deps = ChatCommandDependencies(
        session_manager=deps["session_manager"],
        cache_key=deps["cache_key"],
        plan_dependencies=plan_deps,
        compact_dependencies=compact_deps,
    )
    sm = deps["session_manager"]
    cache_key = deps["cache_key"]
    update_meta_cache = deps["update_meta_cache"]

    async def _send(data: DisplayWireEvent | DisplayEvent) -> None:
        wire = data.to_wire() if isinstance(data, DisplayEvent) else data
        async with _write_lock:
            try:
                await ws.send_json(wire)
            except Exception as e:
                logger.warning(f'[Web] _send failed: {e}, event={wire.get("event")}')

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
        task = asyncio.create_task(_run_chat(sid, username, user_message, ws_name, images, plan_turn, approved_plan))
        if not sm.claim_active_task(session_key or cache_key(username, ws_name, sid), task):
            task.cancel()
            await _send(error_event("当前会话正在生成，请稍后再发送", sid))

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
        _ws_abort_keys.append(run_context.session_key)

        from ..chat_runner import _executor
        future = run_context.loop.run_in_executor(
            _executor,
            run_tool_loop_sync,
            *run_context.executor_args(username=username, workspace=ws_name, plan_turn=plan_turn, approved_plan=approved_plan),
        )

        complete_usage = initial_chat_usage()
        aborted = False
        got_terminal = False
        try:
            while True:
                if run_context.abort_event.is_set():
                    aborted = True
                    break
                try:
                    event = await asyncio.wait_for(run_context.queue.get(), timeout=0.15)
                    queued = normalize_chat_queue_event(event, session_id=sid, usage=complete_usage)
                    complete_usage = queued.usage
                    logger.info(f'[Web] WS dequeue: event={queued.wire["event"]} sid={sid} has_error={bool(queued.wire["data"].get("error"))}')
                    if queued.terminal:
                        logger.debug(f'[Web] terminal event from queue sid={sid} event={queued.wire["event"]}')
                    await _send(queued.wire)
                    logger.info(f'[Web] WS after _send: event={queued.wire["event"]} sid={sid}')
                    if queued.terminal:
                        got_terminal = True
                        logger.info(f'[Web] WS breaking: event={queued.wire["event"]} sid={sid}')
                        break
                except asyncio.TimeoutError:
                    if future.done():
                        for queued in drain_ready_chat_events(run_context.queue, session_id=sid, usage=complete_usage):
                            complete_usage = queued.usage
                            if queued.terminal:
                                logger.debug(f'[Web] drained terminal event sid={sid} event={queued.wire["event"]}')
                            await _send(queued.wire)
                            logger.info(f'[Web] WS drain: event={queued.wire["event"]} sid={sid}')
                            if queued.terminal:
                                got_terminal = True
                                break
                        break
        except Exception as e:
            logger.error(f"[Web] WS chat task error: {e}", exc_info=True)
            await _send(error_event(str(e), sid))

        await finalize_chat_run(
            run_context=run_context,
            future=future,
            session_manager=sm,
            update_meta_cache=update_meta_cache,
            send=_send,
            sid=sid,
            username=username,
            workspace=ws_name,
            usage=complete_usage,
            aborted=aborted,
            got_terminal=got_terminal,
        )

    async def _reader():
        nonlocal ws_closed, _ws_username
        try:
            while not ws_closed:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                    state = await dispatch_chat_ws_message(
                        raw,
                        username=_ws_username,
                        deps=command_deps,
                        send=_send,
                        launch_chat_task=_launch_chat_task,
                    )
                    _ws_username = state.username

                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    logger.debug(f"[Web] WS receive error: {e}")
                    break

        except Exception as e:
            logger.debug(f"[Web] WS reader 退出: {e}")
        finally:
            ws_closed = True

    # 启动 reader
    reader_task = asyncio.create_task(_reader())

    try:
        while not ws_closed:
            await asyncio.sleep(0.5)
    except Exception:
        pass
    finally:
        ws_closed = True
        # 中止所有关联会话
        for key in _ws_abort_keys:
            evt = sm.get_abort_event(key)
            if evt:
                evt.set()
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


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
    from fastapi.responses import JSONResponse, Response
    from urllib.parse import quote

    result = chat_service.export_chat(
        chat_rest_dependencies(),
        session_id=session_id,
        username=username,
        workspace=workspace,
        limit=limit,
        include_thinking=include_thinking,
        include_tools=include_tools,
    )
    if isinstance(result, dict):
        status_code = int(result.get("status_code", 400))
        return JSONResponse({"error": result.get("error", "导出失败")}, status_code=status_code)

    encoded_name = quote(result.filename)
    return Response(
        content=result.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}.md"},
    )
