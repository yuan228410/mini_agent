"""聊天接口 — SSE + WebSocket 双模式，多用户，JSONL 持久化"""
import asyncio
import json
import threading
import uuid

_sessions_lock = threading.Lock()
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.responses import StreamingResponse

from ...config import DATA_DIR, MODEL_CONFIG, STREAMING, RequestContext, get_model_config
from ...llm import get_usage
from ...runner import run_tool_loop
from ...tools import get_definitions
from ...logger import logger
from ..display import WebDisplay

router = APIRouter()

_SYSTEM_PROMPT: str = ""
_SESSION_BASE = DATA_DIR / "web_sessions"
_SESSION_BASE.mkdir(parents=True, exist_ok=True)

_SESSIONS: dict[str, dict[str, list[dict]]] = {}
_SESSION_MODELS: dict[str, str] = {}
_LAST_USAGE: dict[str, dict] = {}
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_DEFAULT_SESSION = "default"

def _inject_todos(messages: list[dict]):
    from ...tools import render_todos
    todos_text = render_todos()
    base = messages[0]["content"]
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[: base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"

def _get_session_lock(username: str, sid: str) -> threading.Lock:
    key = f"{username}:{sid}"
    with _sessions_lock:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = threading.Lock()
        return _SESSION_LOCKS[key]

def _lead_tool_defs() -> list[dict]:
    return [d for d in get_definitions() if d["function"]["name"] not in ("read_inbox", "list_teammates")]

def set_system_prompt(prompt: str):
    global _SYSTEM_PROMPT
    _SYSTEM_PROMPT = prompt

def _user_dir(username: str) -> Path:
    d = _SESSION_BASE / username
    d.mkdir(parents=True, exist_ok=True)
    return d

def _session_file(username: str, session_id: str) -> Path:
    return _user_dir(username) / f"{session_id}.jsonl"

def _load_from_file(username: str, session_id: str) -> list[dict] | None:
    path = _session_file(username, session_id)
    if not path.exists():
        return None
    messages = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return messages if messages else None

def _append_to_file(username: str, session_id: str, msg: dict):
    path = _session_file(username, session_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")

def _write_session_file(username: str, session_id: str, messages: list[dict]):
    path = _session_file(username, session_id)
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

def _ensure_user_sessions(username: str) -> dict[str, list[dict]]:
    with _sessions_lock:
        if username not in _SESSIONS:
            _SESSIONS[username] = {}
        return _SESSIONS[username]

def _get_or_create_session(username: str, session_id: str | None) -> tuple[str, list[dict]]:
    with _sessions_lock:
        if username not in _SESSIONS:
            _SESSIONS[username] = {}
        user_sessions = _SESSIONS[username]
        if session_id and session_id in user_sessions:
            return session_id, user_sessions[session_id]
        sid = session_id or _DEFAULT_SESSION
        if sid in user_sessions:
            return sid, user_sessions[sid]
        loaded = _load_from_file(username, sid)
        if loaded:
            user_sessions[sid] = loaded
            return sid, user_sessions[sid]
        user_sessions[sid] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    _inject_todos(user_sessions[sid])
    _write_session_file(username, sid, user_sessions[sid])
    return sid, user_sessions[sid]

def _run_tool_loop_sync(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                         messages: list[dict], tools: list[dict],
                         max_turns: int = 30, abort_event=None,
                         model_name=None, session_lock=None,
                         session_key: str = "default") -> tuple:
    if session_lock:
        session_lock.acquire()
    try:
        from ...tools.update_todos import set_session
        set_session(session_key)

        disp = WebDisplay(queue, loop)
        cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
        ctx = RequestContext(model_config=cfg, display=disp)

        msg, _ = run_tool_loop(
            messages, tools,
            streaming=STREAMING,
            display=disp,
            inject_fn=_inject_todos,
            abort_event=abort_event,
            max_turns=max_turns,
            ctx=ctx,
        )
        usage = get_usage()
        return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}
    finally:
        if session_lock:
            session_lock.release()

# ── SSE endpoint ──

@router.post("/session")
async def create_session(body: dict):
    username = body.get("username", "default")
    sid = str(uuid.uuid4())[:8]
    user_sessions = _ensure_user_sessions(username)
    user_sessions[sid] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    _inject_todos(user_sessions[sid])
    _write_session_file(username, sid, user_sessions[sid])
    return {"session_id": sid}

@router.get("/sessions")
async def list_sessions(username: str = Query(default="default")):
    user_dir = _user_dir(username)
    sessions = []
    for path in sorted(user_dir.glob("*.jsonl")):
        sid = path.stem
        msgs = _load_from_file(username, sid) or []
        non_system = [m for m in msgs if m["role"] != "system"]
        first_user = next((m.get("content", "")[:50] for m in non_system if m["role"] == "user"), "")
        sessions.append({"session_id": sid, "message_count": len(non_system), "preview": first_user})
    return {"sessions": sessions}

@router.post("/chat")
async def chat_sse_endpoint(body: dict):
    user_message = body.get("message", "").strip()
    username = body.get("username", "default")
    session_id = body.get("session_id")
    if not user_message:
        return {"error": "消息不能为空"}

    sid, messages = _get_or_create_session(username, session_id)
    start_idx = len(messages)
    messages.append({"role": "user", "content": user_message})

    queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    model_name = _SESSION_MODELS.get(f"{username}:{sid}")
    s_lock = _get_session_lock(username, sid)

    async def event_generator():
        try:
            future = loop.run_in_executor(
                None, _run_tool_loop_sync, queue, loop, messages, _lead_tool_defs(), 30, None, model_name, s_lock, f"{username}:{sid}"
            )
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if future.done():
                        break
                    continue

            result = future.result()
            msg, usage = result if isinstance(result, tuple) else (result, get_usage())
            _LAST_USAGE[f"{username}:{sid}"] = usage
            if msg and msg.get("content"):
                messages.append({"role": "assistant", "content": msg["content"]})
            for m in messages[start_idx:]:
                _append_to_file(username, sid, m)
            done_data = {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"], "session_id": sid}
            yield f"event: done\ndata: {json.dumps(done_data, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[Web] SSE 错误: {e}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ── WebSocket endpoint ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()

    incoming: asyncio.Queue = asyncio.Queue()
    ws_closed = False

    async def _reader():
        nonlocal ws_closed
        try:
            while not ws_closed:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
                    await incoming.put(raw)
                except asyncio.TimeoutError:
                    pass
        except Exception:
            pass
        finally:
            if not ws_closed:
                await incoming.put(None)

    reader_task = asyncio.create_task(_reader())

    try:
        while True:
            raw = await incoming.get()
            if raw is None:
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"event": "error", "data": {"error": "无效 JSON"}})
                continue

            msg_type = data.get("type")

            if msg_type == "abort":
                continue

            if msg_type != "chat":
                continue

            user_message = data.get("message", "").strip()
            username = data.get("username", "default")
            session_id = data.get("session_id")
            if not user_message:
                await ws.send_json({"event": "error", "data": {"error": "消息不能为空"}})
                continue

            sid, messages = _get_or_create_session(username, session_id)
            start_idx = len(messages)
            messages.append({"role": "user", "content": user_message})

            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()
            abort_event = threading.Event()

            model_name = _SESSION_MODELS.get(f"{username}:{sid}")
            s_lock = _get_session_lock(username, sid)
            future = loop.run_in_executor(
                None, _run_tool_loop_sync, queue, loop, messages, _lead_tool_defs(), 30, abort_event, model_name, s_lock, f"{username}:{sid}"
            )

            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.1)
                        await ws.send_json(event)
                        if event["event"] in ("done", "aborted", "error"):
                            break
                    except asyncio.TimeoutError:
                        if future.done():
                            break

                    try:
                        raw2 = incoming.get_nowait()
                        d = json.loads(raw2) if raw2 else {}
                        if d.get("type") == "abort":
                            abort_event.set()
                    except (asyncio.QueueEmpty, json.JSONDecodeError):
                        pass

                    if abort_event.is_set():
                        await ws.send_json({"event": "aborted", "data": {}})
                        break
            except Exception as e:
                logger.error(f"[Web] WS chat 错误: {e}")
                await ws.send_json({"event": "error", "data": {"error": str(e)}})

            try:
                result = future.result()
                msg, usage = result if isinstance(result, tuple) else (result, {"prompt_tokens": 0, "completion_tokens": 0})
            except Exception:
                msg = None
                usage = {"prompt_tokens": 0, "completion_tokens": 0}
            _LAST_USAGE[f"{username}:{sid}"] = usage
            if msg and msg.get("content"):
                messages.append({"role": "assistant", "content": msg["content"]})
            for m in messages[start_idx:]:
                _append_to_file(username, sid, m)

            if not abort_event.is_set():
                done_data = {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"], "session_id": sid}
                await ws.send_json({"event": "done", "data": done_data})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[Web] WS 错误: {e}")
    finally:
        ws_closed = True
        reader_task.cancel()

# ── History & Reset ──

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=_DEFAULT_SESSION), username: str = Query(default="default")):
    _, messages = _get_or_create_session(username, session_id)
    history = []
    for m in messages:
        if m["role"] in ("system", "tool"):
            continue
        if m.get("tool_calls"):
            continue
        entry: dict = {"role": m["role"]}
        if m.get("content"):
            entry["content"] = m["content"]
        if m.get("thinking"):
            entry["thinking"] = m["thinking"]
        history.append(entry)
    return {"session_id": session_id, "history": history}

@router.post("/chat/reset")
async def chat_reset(body: dict | None = None):
    body = body or {}
    username = body.get("username", "default")
    session_id = body.get("session_id", _DEFAULT_SESSION)
    sid, messages = _get_or_create_session(username, session_id)
    system_content = messages[0]["content"]
    user_sessions = _ensure_user_sessions(username)
    user_sessions[sid] = [{"role": "system", "content": system_content}]
    _inject_todos(user_sessions[sid])
    _write_session_file(username, sid, user_sessions[sid])
    return {"status": "ok", "session_id": sid}
