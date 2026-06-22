"""聊天接口 — WebSocket 模式 + 聊天历史/导出/重置

从原 1446 行拆分后，本文件只保留：
- WebSocket endpoint (chat_ws_endpoint)
- _run_chat / _reader 协程
- REST: chat_history, chat_export, chat_reset
"""
import asyncio
import json

from fastapi import APIRouter, Query, WebSocket

from ...application import chat_compact_service, chat_service, plan_command_service
from ...core.events import DisplayEvent, DisplayEventType
from ...core.runtime_types import DisplayWireEvent, PlanArtifactDict
from ...logger import logger
from ..route_types import (
    ChatHistoryResponse,
    ChatResetRequest,
    ChatResetResponse,
    ImageUpload,
    RouteErrorResponse,
)
from ..chat_events import drain_ready_chat_events, initial_chat_usage, normalize_chat_queue_event
from ..chat_run_context import prepare_chat_run_context
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
    sm = deps["session_manager"]
    cache_key = deps["cache_key"]
    resolve_base = deps["resolve_base"]
    update_meta_cache = deps["update_meta_cache"]

    def _ws_event(event: DisplayEventType | str, **data) -> DisplayWireEvent:
        event_type = event if isinstance(event, DisplayEventType) else DisplayEventType(event)
        return DisplayEvent(event_type, data).to_wire()

    def _error_event(error: str, session_id: str | None = None) -> DisplayWireEvent:
        payload = {"error": error}
        if session_id:
            payload["session_id"] = session_id
        return DisplayEvent(DisplayEventType.ERROR, payload).to_wire()

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
            await _send(_error_event("当前会话正在生成，请稍后再发送", sid))

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
            await _send(_error_event(str(e), sid))

        if aborted:
            logger.info(f"[Web] chat aborted sid={sid}")
            await _send(_ws_event(DisplayEventType.ABORTED, session_id=sid))
            try:
                future.cancel()
            except Exception:
                pass

        usage = complete_usage
        sm.set_last_usage(run_context.session_key, usage)
        update_meta_cache(username, sid, ws_name, run_context.messages)

        logger.info(f"[Web] WS loop exit: aborted={aborted} got_terminal={got_terminal} sid={sid}")
        if not aborted and not got_terminal:
            try:
                future.result()
            except Exception as e:
                logger.error(f"[Web] chat runner ended without terminal event: {e}", exc_info=True)
                await _send(_error_event(str(e), sid))
            else:
                logger.debug(f"[Web] sending done sid={sid} usage={usage}")
                await _send(_ws_event(
                    DisplayEventType.DONE,
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    session_id=sid,
                ))

        current_task = asyncio.current_task()
        sm.release_active_task(run_context.session_key, current_task)

    async def _reader():
        nonlocal ws_closed, _ws_username
        try:
            while not ws_closed:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        await _send(_error_event("无效 JSON"))
                        continue

                    msg_type = data.get("type")

                    if msg_type == "login":
                        u = data.get("username", "").strip()
                        if u:
                            _ws_username = u
                        continue

                    if msg_type == "ping":
                        await _send(_ws_event(DisplayEventType.PONG))
                        continue

                    if not _ws_username:
                        await _send(_error_event("请先发送 login 消息"))
                        continue

                    if msg_type == "abort":
                        abort_sid = data.get("session_id")
                        abort_username = _ws_username
                        abort_ws = data.get("workspace")
                        if abort_sid:
                            evt = sm.get_abort_event(cache_key(abort_username, abort_ws, abort_sid))
                            if evt:
                                evt.set()
                        continue

                    username = _ws_username
                    session_id = data.get("session_id")
                    ws_name = data.get("workspace")

                    if msg_type and msg_type.startswith("plan."):
                        result = plan_command_service.handle_plan_command(
                            plan_deps,
                            username=username,
                            session_id=session_id,
                            workspace=ws_name,
                            msg_type=msg_type,
                            payload=data,
                        )
                        for event in result.events:
                            await _send(event)
                        if result.run:
                            run = result.run
                            await _launch_chat_task(run.sid, run.username, run.user_message, run.workspace, run.images, run.plan_turn, run.approved_plan, result.session_key)
                        continue

                    if msg_type != "chat":
                        continue

                    user_message = data.get("message", "").strip()
                    images = data.get("images")

                    # 处理 /compact 命令
                    if user_message == "/compact":
                        ws_name = data.get("workspace")
                        result = chat_compact_service.compact_chat(compact_deps, username=username, session_id=session_id, workspace=ws_name)
                        await _send(result.event)
                        continue

                    # 处理 /act 命令（批准当前计划并执行）
                    if user_message == "/act":
                        ws_name = data.get("workspace")
                        result = plan_command_service.approve_current_plan(plan_deps, username=username, session_id=session_id, workspace=ws_name)
                        for event in result.events:
                            await _send(event)
                        if result.run:
                            run = result.run
                            await _launch_chat_task(run.sid, run.username, run.user_message, run.workspace, run.images, run.plan_turn, run.approved_plan, result.session_key)
                        continue

                    if not session_id:
                        await _send(_error_event("请先选择会话"))
                        continue

                    ws_name = data.get("workspace")
                    sid = session_id

                    # 同一会话只允许一个生成任务，避免多个 runner 同时改同一 messages 导致串流
                    await _launch_chat_task(sid, username, user_message, ws_name, images)

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
