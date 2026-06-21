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

from ...application import chat_service
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
from ..runtime_helpers import chat_rest_dependencies, chat_session_dependencies, request_context_for_settings, settings_for_model
from ...plan.service import PlanService
from ...plan.store import PlanStore

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

    def _plan_event(kind: str, session_id: str | None = None, **data) -> DisplayWireEvent:
        payload = {"kind": kind}
        payload.update(data)
        if session_id:
            payload["session_id"] = session_id
        return DisplayEvent(DisplayEventType.PLAN_EVENT, payload).to_wire()

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
                        if not session_id:
                            await _send(_error_event("请先选择会话"))
                            continue
                        base = resolve_base(username, ws_name)
                        sid, messages = get_or_create_session(username, session_id, base, ws_name)
                        comp = get_or_create_components(username, sid, base, ws_name)
                        session_key = cache_key(username, ws_name, sid)
                        store = PlanStore(comp["history_db"], ws_name or "default", sid)
                        plan_svc = PlanService()

                        try:
                            if msg_type == "plan.start":
                                artifact = plan_svc.start(session_key=session_key, sm=sm, store=store, goal=data.get("goal", ""))
                                await _send(_plan_event("state.changed", sid, state=artifact.status, mode="plan"))
                                await _send(_plan_event("artifact.updated", sid, plan=artifact.to_dict()))
                                continue
                            if msg_type == "plan.select_option":
                                artifact = plan_svc.select_option(session_key=session_key, sm=sm, store=store, plan_id=data.get("plan_id", ""), option_id=data.get("option_id", ""))
                                await _send(_plan_event("option.selected", sid, option_id=artifact.selected_option_id, plan=artifact.to_dict()))
                                await _send(_plan_event("approval.required", sid, plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision))
                                continue
                            if msg_type == "plan.apply_decision":
                                artifact = plan_svc.apply_decision(
                                    session_key=session_key,
                                    sm=sm,
                                    store=store,
                                    plan_id=data.get("plan_id", ""),
                                    step_id=data.get("step_id", ""),
                                    decision_id=data.get("decision_id", ""),
                                    selected_option_ids=data.get("selected_option_ids") or [],
                                    custom_value=data.get("custom_value", ""),
                                    revision=int(data["revision"]) if data.get("revision") is not None else None,
                                )
                                await _send(_plan_event("decision.applied", sid, plan=artifact.to_dict(), step_id=data.get("step_id", ""), decision_id=data.get("decision_id", "")))
                                await _send(_plan_event("artifact.updated", sid, plan=artifact.to_dict()))
                                if artifact.status == "awaiting_approval":
                                    await _send(_plan_event("approval.required", sid, plan=artifact.to_dict(), plan_id=artifact.plan_id, revision=artifact.revision))
                                continue
                            if msg_type == "plan.cancel":
                                plan_svc.cancel(session_key=session_key, sm=sm, store=store)
                                await _send(_plan_event("cancelled", sid, mode="chat"))
                                continue
                            if msg_type == "plan.approve":
                                artifact = plan_svc.approve(session_key=session_key, sm=sm, store=store, plan_id=data.get("plan_id", ""), revision=int(data.get("revision") or 0))
                                executing_artifact = plan_svc.mark_executing(session_key=session_key, sm=sm, store=store, artifact=artifact)
                                await _send(_plan_event("approved", sid, plan=artifact.to_dict()))
                                await _send(_plan_event("execution.started", sid, plan=executing_artifact.to_dict(), mode="execute"))
                                task = asyncio.create_task(_run_chat(sid, username, "", ws_name, None, False, executing_artifact.to_dict()))
                                if not sm.claim_active_task(session_key, task):
                                    task.cancel()
                                    await _send(_error_event("当前会话正在生成，请稍后再发送", sid))
                                continue
                            if msg_type in ("plan.message", "plan.revise"):
                                user_message = data.get("message", "").strip()
                                if not user_message:
                                    await _send(_error_event("计划消息不能为空", sid))
                                    continue
                                task = asyncio.create_task(_run_chat(sid, username, user_message, ws_name, data.get("images"), True, None))
                                if not sm.claim_active_task(session_key, task):
                                    task.cancel()
                                    await _send(_error_event("当前会话正在生成，请稍后再发送", sid))
                                continue
                        except Exception as e:
                            await _send(_plan_event("error", sid, error=str(e)))
                            continue

                    if msg_type != "chat":
                        continue

                    user_message = data.get("message", "").strip()
                    images = data.get("images")

                    # 处理 /compact 命令
                    if user_message == "/compact":
                        ws_name = data.get("workspace")
                        if not session_id:
                            await _send(_error_event("请先选择会话"))
                            continue

                        sid, messages = get_or_create_session(username, session_id, workspace=ws_name, create=False)
                        if messages is None:
                            await _send(_error_event(f"会话 {session_id} 不存在"))
                            continue

                        comp = get_or_create_components(username, sid, resolve_base(username, ws_name), ws_name)
                        non_system = [m for m in messages if m["role"] != "system"]

                        if len(non_system) <= comp["compactor"].keep_recent:
                            await _send(_ws_event(DisplayEventType.INFO, message=f"消息数({len(non_system)})未超过保留阈值({comp['compactor'].keep_recent})，无需压缩", session_id=sid))
                            continue

                        before = len(non_system)
                        session_key = cache_key(username, ws_name, sid)
                        model_name = sm.get_model(session_key)
                        settings = settings_for_model(comp["settings"], model_name)
                        ctx = request_context_for_settings(settings, display=None)

                        try:
                            from ...llm import chat
                            messages[:] = comp["compactor"].compact(chat, messages, ctx=ctx)
                            after = len([m for m in messages if m["role"] != "system"])
                            await _send(_ws_event(DisplayEventType.INFO, message=f"✅ 压缩完成：{before} → {after} 条消息", session_id=sid))
                        except Exception as e:
                            await _send(_error_event(f"压缩失败: {e}", sid))
                        continue

                    # 处理 /act 命令（批准当前计划并执行）
                    if user_message == "/act":
                        ws_name = data.get("workspace")
                        if not session_id:
                            await _send(_error_event("请先选择会话"))
                            continue
                        base = resolve_base(username, ws_name)
                        sid, _ = get_or_create_session(username, session_id, base, ws_name)
                        comp = get_or_create_components(username, sid, base, ws_name)
                        session_key = cache_key(username, ws_name, sid)
                        store = PlanStore(comp["history_db"], ws_name or "default", sid)
                        artifact_dict = sm.get_plan_state(session_key).current_plan or store.current()
                        if not artifact_dict:
                            await _send(_plan_event("error", sid, error="当前没有可审批的计划"))
                            continue
                        try:
                            plan_svc = PlanService()
                            artifact = plan_svc.approve(
                                session_key=session_key,
                                sm=sm,
                                store=store,
                                plan_id=artifact_dict.get("plan_id", ""),
                                revision=int(artifact_dict.get("revision") or 0),
                            )
                            executing_artifact = plan_svc.mark_executing(session_key=session_key, sm=sm, store=store, artifact=artifact)
                            await _send(_plan_event("approved", sid, plan=artifact.to_dict()))
                            await _send(_plan_event("execution.started", sid, plan=executing_artifact.to_dict(), mode="execute"))
                            task = asyncio.create_task(_run_chat(sid, username, "", ws_name, None, False, executing_artifact.to_dict()))
                            if not sm.claim_active_task(session_key, task):
                                task.cancel()
                                await _send(_error_event("当前会话正在生成，请稍后再发送", sid))
                        except Exception as e:
                            await _send(_plan_event("error", sid, error=str(e)))
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
