"""聊天接口 — WebSocket 模式 + 聊天历史/导出/重置

从原 1446 行拆分后，本文件只保留：
- WebSocket endpoint (chat_ws_endpoint)
- _run_chat / _reader 协程
- REST: chat_history, chat_export, chat_reset
"""
import asyncio
import json
import threading

from fastapi import APIRouter, Query, WebSocket

from ...application import chat_compact_service, chat_service, plan_command_service
from ...core.events import DisplayEvent, DisplayEventType
from ...core.runtime_types import DisplayWireEvent, MessageDict, PlanArtifactDict
from ...logger import logger
from ...utils import now_ts
from ..route_types import (
    ChatHistoryResponse,
    ChatResetRequest,
    ChatResetResponse,
    ImageUpload,
    RouteErrorResponse,
)
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
    get_or_create_session = deps["get_or_create_session"]
    get_or_create_components = deps["get_or_create_components"]
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

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None, images: list[ImageUpload] | None = None, plan_turn: bool = False, approved_plan: PlanArtifactDict | None = None) -> None:
        logger.info(f"[Web] WS _run_chat sid={sid} user={username} ws={ws_name} images={len(images) if images else 0} plan_turn={plan_turn} approved={bool(approved_plan)}")
        session_key = cache_key(username, ws_name, sid)
        base = resolve_base(username, ws_name)
        messages = get_or_create_session(username, sid, base, ws_name)[1]
        ts = now_ts()

        # 构造用户消息（可能包含图片）。审批后的执行由 approved_plan 注入内部指令，不追加可见用户消息。
        if user_message or images:
            user_msg: MessageDict = {"role": "user", "content": user_message, "timestamp": ts}
            if images and len(images) > 0:
                content_blocks = [{"type": "text", "text": user_message}]
                for img in images:
                    data_url = img.get("dataUrl", "")
                    if data_url.startswith("data:"):
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": data_url}
                        })
                user_msg["content"] = content_blocks

            messages.append(user_msg)
        get_or_create_components(username, sid, base, ws_name)

        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_event_loop()

        abort_event = sm.get_abort_event(session_key)
        if abort_event is None:
            abort_event = threading.Event()
        else:
            abort_event.clear()  # 上次 abort 后重置，否则新请求立即被中止

        model_name = sm.get_model(session_key)
        _ws_abort_keys.append(session_key)

        s_lock = sm.get_lock(session_key)
        comp = get_or_create_components(username, sid, base, ws_name)
        settings = comp.get("settings")
        max_turns_web = settings.web.max_turns if settings else 10
        from ..chat_runner import _executor
        future = loop.run_in_executor(
            _executor, run_tool_loop_sync, queue, loop, messages, None, max_turns_web, abort_event, model_name, s_lock, session_key, username, ws_name, plan_turn, approved_plan
        )

        complete_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        aborted = False
        got_terminal = False
        try:
            while True:
                if abort_event.is_set():
                    aborted = True
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.15)
                    event["data"]["session_id"] = sid
                    logger.info(f'[Web] WS dequeue: event={event["event"]} sid={sid} has_error={bool(event["data"].get("error"))}')
                    if event["event"] == "complete" and "prompt_tokens" in event["data"]:
                        complete_usage = {"prompt_tokens": event["data"]["prompt_tokens"], "completion_tokens": event["data"].get("completion_tokens", 0)}
                    if event["event"] in ("done", "aborted", "error", "complete"):
                        logger.debug(f'[Web] terminal event from queue sid={sid} event={event["event"]}')
                    await _send(event)
                    logger.info(f'[Web] WS after _send: event={event["event"]} sid={sid}')
                    if event["event"] in ("done", "aborted", "error", "complete"):
                        got_terminal = True
                        logger.info(f'[Web] WS breaking: event={event["event"]} sid={sid}')
                        break
                except asyncio.TimeoutError:
                    if future.done():
                        for _ in range(10):
                            try:
                                event = queue.get_nowait()
                                event["data"]["session_id"] = sid
                                if event["event"] == "complete" and "prompt_tokens" in event["data"]:
                                    complete_usage = {"prompt_tokens": event["data"]["prompt_tokens"], "completion_tokens": event["data"].get("completion_tokens", 0)}
                                if event["event"] in ("done", "aborted", "error", "complete"):
                                    logger.debug(f'[Web] drained terminal event sid={sid} event={event["event"]}')
                                await _send(event)
                                logger.info(f'[Web] WS drain: event={event["event"]} sid={sid}')
                                if event["event"] in ("done", "aborted", "error", "complete"):
                                    got_terminal = True
                                    break
                            except asyncio.QueueEmpty:
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
        sm.set_last_usage(session_key, usage)
        update_meta_cache(username, sid, ws_name, messages)

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
        sm.release_active_task(session_key, current_task)

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
                            task = asyncio.create_task(_run_chat(run.sid, run.username, run.user_message, run.workspace, run.images, run.plan_turn, run.approved_plan))
                            if not sm.claim_active_task(result.session_key or cache_key(username, ws_name, run.sid), task):
                                task.cancel()
                                await _send(_error_event("当前会话正在生成，请稍后再发送", run.sid))
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
                            task = asyncio.create_task(_run_chat(run.sid, run.username, run.user_message, run.workspace, run.images, run.plan_turn, run.approved_plan))
                            if not sm.claim_active_task(result.session_key or cache_key(username, ws_name, run.sid), task):
                                task.cancel()
                                await _send(_error_event("当前会话正在生成，请稍后再发送", run.sid))
                        continue

                    if not session_id:
                        await _send(_error_event("请先选择会话"))
                        continue

                    ws_name = data.get("workspace")
                    sid = session_id

                    # 同一会话只允许一个生成任务，避免多个 runner 同时改同一 messages 导致串流
                    session_key = cache_key(username, ws_name, sid)
                    task = asyncio.create_task(
                        _run_chat(sid, username, user_message, ws_name, images)
                    )
                    if not sm.claim_active_task(session_key, task):
                        task.cancel()
                        await _send(_error_event("当前会话正在生成，请稍后再发送", sid))
                        continue

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
