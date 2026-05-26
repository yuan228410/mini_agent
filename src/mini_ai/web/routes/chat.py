"""聊天 SSE 流式接口 — 多用户 + JSONL 持久化"""
import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from ...config import DATA_DIR, MODEL_CONFIG, STREAMING
from ...llm import chat_stream, chat, _get_usage
from ...tools import handle_tool_calls, get_definitions, register_display
from ...logger import logger
from ..display import WebDisplay

router = APIRouter()

_SYSTEM_PROMPT: str = ""
_SESSION_BASE = DATA_DIR / "web_sessions"
_SESSION_BASE.mkdir(parents=True, exist_ok=True)

_SESSIONS: dict[str, dict[str, list[dict]]] = {}
_DEFAULT_SESSION = "default"

def _inject_todos(messages: list[dict]):
    from ...tools import render_todos
    todos_text = render_todos()
    base = messages[0]["content"]
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[: base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"

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
    if username not in _SESSIONS:
        _SESSIONS[username] = {}
    return _SESSIONS[username]

def _get_or_create_session(username: str, session_id: str | None) -> tuple[str, list[dict]]:
    user_sessions = _ensure_user_sessions(username)
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
                         messages: list[dict], tools: list[dict], max_turns: int = 30):
    disp = WebDisplay(queue, loop)
    register_display(disp)

    for turn in range(max_turns):
        msg = None
        if STREAMING:
            thinking_seen = False
            for chunk in chat_stream(messages, tools=tools):
                if chunk["type"] == "thinking_start":
                    disp.thinking_start()
                    thinking_seen = True
                elif chunk["type"] == "thinking":
                    disp.thinking_chunk(chunk["content"])
                elif chunk["type"] == "thinking_end":
                    disp.thinking_end()
                    thinking_seen = False
                elif chunk["type"] == "text":
                    disp.text_chunk(chunk["content"])
                elif chunk["type"] == "error":
                    disp._push("error", {"error": chunk["error"]})
                    return None
                elif chunk["type"] == "done":
                    msg = chunk["msg"]
                    if thinking_seen:
                        disp.thinking_end()
        else:
            msg = chat(messages, tools=tools)
            if msg and msg.get("thinking"):
                disp.thinking_start()
                disp._thinking_buf = msg["thinking"]
                disp.thinking_end()

        if not msg or "tool_calls" not in msg:
            disp.text_end()
            return msg

        handle_tool_calls(msg, messages)
        _inject_todos(messages)

    disp._push("error", {"error": f"工具循环达到上限 {max_turns} 轮"})
    return None

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
        messages = _load_from_file(username, sid) or []
        non_system = [m for m in messages if m["role"] != "system"]
        first_user = next((m.get("content", "")[:50] for m in non_system if m["role"] == "user"), "")
        sessions.append({"session_id": sid, "message_count": len(non_system), "preview": first_user})
    return {"sessions": sessions}

@router.post("/chat")
async def chat_endpoint(body: dict):
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

    async def event_generator():
        try:
            future = loop.run_in_executor(
                None, _run_tool_loop_sync, queue, loop, messages, _lead_tool_defs()
            )

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if future.done():
                        break
                    continue

            msg = future.result()
            usage = _get_usage()

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
