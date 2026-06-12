"""聊天接口 — WebSocket 模式 + 聊天历史/导出/重置

从原 1446 行拆分后，本文件只保留：
- WebSocket endpoint (chat_ws_endpoint)
- _run_chat / _reader 协程
- REST: chat_history, chat_export, chat_reset
"""
import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket

from ...config import DATA_DIR, MODEL_CONFIG, PLAN, STREAMING, RequestContext, get_model_config
from ...llm import get_usage, reset_usage, chat as llm_chat
from ...logger import logger
from ...tools import inject_todos as _inject_todos
from ...utils import now_ts
from ..session_manager import (
    SessionManager, cache_key, ws_key, safe_queue_put,
    resolve_base, get_or_create_session, get_or_create_components,
    build_system_prompt, lead_tool_defs,
    _load_session_name, _save_session_name,
    _update_meta_cache, _build_meta,
)
from ..chat_runner import run_tool_loop_sync

router = APIRouter()


# ── WebSocket endpoint ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()
    _active_tasks: dict[str, asyncio.Task] = {}
    _ws_abort_keys: list[str] = []
    ws_closed = False
    _write_lock = asyncio.Lock()
    _ws_username: str | None = None

    async def _send(data: dict):
        async with _write_lock:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.warning(f'[Web] _send failed: {e}, event={data.get("event")}')

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None, images: list | None = None):
        logger.info(f"[Web] WS _run_chat sid={sid} user={username} ws={ws_name} images={len(images) if images else 0}")
        session_key = cache_key(username, ws_name, sid)
        base = resolve_base(username, ws_name)
        messages = get_or_create_session(username, sid, base, ws_name)[1]
        ts = now_ts()

        # 构造用户消息（可能包含图片）
        user_msg: dict = {"role": "user", "content": user_message, "timestamp": ts}
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

        sm = SessionManager.instance()
        abort_event = sm.get_abort_event(session_key)
        if abort_event is None:
            abort_event = threading.Event()

        model_name = sm.get_model(session_key)
        _ws_abort_keys.append(session_key)

        s_lock = sm.get_lock(session_key)
        from ...config import RUNNER
        max_turns_web = RUNNER.get("max_turns", 20)
        future = loop.run_in_executor(
            None, run_tool_loop_sync, queue, loop, messages, None, max_turns_web, abort_event, model_name, s_lock, session_key, username, ws_name
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
                    if event["event"] in ("done", "aborted", "complete"):
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
                                if event["event"] in ("done", "aborted", "complete"):
                                    got_terminal = True
                                    break
                            except asyncio.QueueEmpty:
                                break
                        break
        except Exception as e:
            logger.error(f"[Web] WS chat task error: {e}", exc_info=True)
            await _send({"event": "error", "data": {"error": str(e), "session_id": sid}})

        if aborted:
            logger.info(f"[Web] chat aborted sid={sid}")
            await _send({"event": "aborted", "data": {"session_id": sid}})
            try:
                future.cancel()
            except Exception:
                pass

        usage = complete_usage
        sm.set_last_usage(session_key, usage)
        _update_meta_cache(username, sid, ws_name, messages)

        logger.info(f"[Web] WS loop exit: aborted={aborted} got_terminal={got_terminal} sid={sid}")
        if not aborted and not got_terminal:
            logger.debug(f"[Web] sending done sid={sid} usage={usage}")
            await _send({
                "event": "done",
                "data": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"], "session_id": sid}
            })

        _active_tasks.pop(session_key, None)

    async def _reader():
        nonlocal ws_closed, _ws_username
        try:
            while not ws_closed:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        await _send({"event": "error", "data": {"error": "无效 JSON"}})
                        continue

                    msg_type = data.get("type")

                    if msg_type == "login":
                        u = data.get("username", "").strip()
                        if u:
                            _ws_username = u
                        continue

                    if msg_type == "ping":
                        await _send({"event": "pong", "data": {}})
                        continue

                    if not _ws_username:
                        await _send({"event": "error", "data": {"error": "请先发送 login 消息"}})
                        continue

                    if msg_type == "abort":
                        abort_sid = data.get("session_id")
                        abort_username = _ws_username
                        abort_ws = data.get("workspace")
                        if abort_sid:
                            sm = SessionManager.instance()
                            evt = sm.get_abort_event(cache_key(abort_username, abort_ws, abort_sid))
                            if evt:
                                evt.set()
                        continue

                    if msg_type != "chat":
                        continue

                    user_message = data.get("message", "").strip()
                    username = _ws_username
                    session_id = data.get("session_id")
                    images = data.get("images")

                    # 处理 /compact 命令
                    if user_message == "/compact":
                        ws_name = data.get("workspace")
                        if not session_id:
                            await _send({"event": "error", "data": {"error": "请先选择会话"}})
                            continue

                        sid, messages = get_or_create_session(username, session_id, workspace=ws_name, create=False)
                        if messages is None:
                            await _send({"event": "error", "data": {"error": f"会话 {session_id} 不存在"}})
                            continue

                        comp = get_or_create_components(username, sid, resolve_base(username, ws_name), ws_name)
                        non_system = [m for m in messages if m["role"] != "system"]

                        if len(non_system) <= comp["compactor"].keep_recent:
                            await _send({"event": "info", "data": {"message": f"消息数({len(non_system)})未超过保留阈值({comp['compactor'].keep_recent})，无需压缩", "session_id": sid}})
                            continue

                        before = len(non_system)
                        session_key = cache_key(username, ws_name, sid)
                        sm = SessionManager.instance()
                        model_name = sm.get_model(session_key)
                        cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
                        ctx = RequestContext(model_config=cfg, display=None)

                        try:
                            from ...llm import chat
                            messages[:] = comp["compactor"].compact(chat, messages, ctx=ctx)
                            after = len([m for m in messages if m["role"] != "system"])
                            await _send({"event": "info", "data": {"message": f"✅ 压缩完成：{before} → {after} 条消息", "session_id": sid}})
                        except Exception as e:
                            await _send({"event": "error", "data": {"error": f"压缩失败: {e}", "session_id": sid}})
                        continue

                    # 处理 /act 命令（切换到执行模式）
                    if user_message == "/act":
                        ws_name = data.get("workspace")
                        if session_id:
                            session_key = cache_key(username, ws_name, session_id)
                            sm = SessionManager.instance()
                            sm.set_plan_mode(session_key, False)
                        await _send({"event": "info", "data": {"message": "⚡ 已切换到执行模式", "session_id": session_id}})
                        continue

                    if not session_id:
                        await _send({"event": "error", "data": {"error": "请先选择会话"}})
                        continue

                    ws_name = data.get("workspace")

                    # 取消正在生成的请求
                    sm = SessionManager.instance()
                    session_key = cache_key(username, ws_name, session_id)
                    existing = _active_tasks.get(session_key)
                    if existing and not existing.done():
                        evt = sm.get_abort_event(session_key)
                        if evt:
                            evt.set()
                        try:
                            await asyncio.wait_for(existing, timeout=5.0)
                        except (asyncio.TimeoutError, Exception):
                            existing.cancel()

                    task = asyncio.create_task(
                        _run_chat(session_id, username, user_message, ws_name, images)
                    )
                    _active_tasks[session_key] = task

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
        sm = SessionManager.instance()
        for key in _ws_abort_keys:
            evt = sm.get_abort_event(key)
            if evt:
                evt.set()
        reader_task.cancel()
        try:
            await reader_task
        except Exception:
            pass


# ── REST: 聊天历史 ──

_MAX_HISTORY_LOAD = 2000

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")):
    _t0 = time.time()
    if not username:
        return {"session_id": session_id, "history": []}
    if not session_id:
        return {"session_id": "", "history": []}
    try:
        base = resolve_base(username, workspace or None)
    except Exception:
        return {"session_id": session_id, "history": []}

    key = cache_key(username, workspace or None, session_id)
    sm = SessionManager.instance()
    messages = sm.get_messages(key)
    if messages is None:
        messages = get_or_create_session(username, session_id, base, workspace or None)[1]
    if not messages:
        messages = []

    non_system = [m for m in messages if m["role"] not in ("system", "tool")]
    history = []
    for m in non_system:
        entry: dict = {"role": m["role"]}
        content = m.get("content")

        if isinstance(content, list):
            text_parts = []
            images = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        img_url = part.get("image_url", {}).get("url", "")
                        if img_url:
                            images.append({"dataUrl": img_url, "name": "", "size": 0})
            entry["content"] = "\n".join(text_parts)
            if images:
                entry["images"] = images
        else:
            if content:
                entry["content"] = content

        if m.get("timestamp"):
            entry["timestamp"] = m["timestamp"]
        if m.get("thinking"):
            entry["thinking"] = m["thinking"]
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        history.append(entry)

    logger.info(f"[chat_history] sid={session_id} ws={workspace} msgs={len(messages)} time={time.time()-_t0:.3f}s")
    return {"session_id": session_id, "history": history}


# ── REST: 重置会话 ──

@router.post("/chat/reset")
async def chat_reset(body: dict | None = None):
    body = body or {}
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    ws = workspace or None
    base = resolve_base(username, ws)
    sid, messages = get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    system_content = messages[0]["content"]
    old_name = messages[0].get("name", "")
    key = cache_key(username, ws, sid)

    sm = SessionManager.instance()
    sm.reset_session(key, system_content, old_name)
    msgs = sm.get_messages(key)
    if msgs:
        _inject_todos(msgs)
    _update_meta_cache(username, sid, ws, msgs)

    return {"status": "ok", "session_id": sid}


# ── REST: 导出会话 ──

@router.get("/chat/export")
async def chat_export(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default=""), limit: int = Query(default=0), include_thinking: bool = Query(default=False), include_tools: bool = Query(default=False)):
    from fastapi.responses import JSONResponse, Response
    if not username:
        return JSONResponse({"error": "缺少 username"}, status_code=400)
    if not session_id:
        return JSONResponse({"error": "缺少 session_id"}, status_code=400)
    try:
        base = resolve_base(username, workspace or None)
    except Exception as e:
        logger.error(f"[export] resolve_base error: {e}")
        return JSONResponse({"error": f"工作空间错误: {e}"}, status_code=400)

    comp = get_or_create_components(username, session_id, base, workspace or None)
    messages = comp["history_db"].load_session(workspace or "default", session_id, limit=limit) or []
    if not messages:
        return JSONResponse({"error": f"会话 '{session_id}' 不存在或无消息"}, status_code=404)

    session_name = _load_session_name(base, session_id)
    if not session_name:
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                session_name = m["content"][:50]
                break
    if not session_name:
        session_name = session_id

    lines = [f"# {session_name}\n"]
    for m in messages:
        role = m.get("role", "")
        content = m.get("content") or ""
        ts = m.get("timestamp", "")
        if role in ("system", "tool"):
            continue
        if role == "user":
            label = f"**🧑 用户**"
            if ts:
                label += f"  `{ts}`"
            lines.append(f"\n{label}\n\n{content}\n")
        elif role == "assistant":
            thinking = m.get("thinking")
            tool_calls = m.get("tool_calls")
            has_thinking = include_thinking and thinking
            has_tools = include_tools and tool_calls
            if not content and not has_thinking and not has_tools:
                continue
            label = f"**🤖 助手**"
            if ts:
                label += f"  `{ts}`"
            lines.append(f"\n{label}\n")
            if has_thinking:
                thinking_text = thinking if isinstance(thinking, str) else str(thinking)
                lines.append(f"\n<details>\n<summary>💭 思考过程</summary>\n\n{thinking_text}\n\n</details>\n")
            if has_tools:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args = str(fn.get("arguments", ""))
                    result = tc.get("_result", "")
                    lines.append(f"\n> 🔧 **{name}**({args[:200]})\n")
                    if result:
                        lines.append(f"> 结果: {result[:500]}\n")
            if content:
                lines.append(f"\n{content}\n")

    md_content = "\n".join(lines)
    from urllib.parse import quote
    safe_name = session_name.replace("/", "-").replace(" ", "-")[:60]
    encoded_name = quote(safe_name)
    return Response(
        content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}.md"},
    )
