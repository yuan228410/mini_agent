"""聊天 SSE 流式接口 — 多会话隔离"""
import asyncio
import json
import uuid

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from ...config import MODEL_CONFIG, STREAMING
from ...llm import chat_stream, chat, _get_usage
from ...tools import handle_tool_calls, get_definitions, register_display
from ...logger import logger
from ..display import WebDisplay

router = APIRouter()

_SYSTEM_PROMPT: str = ""
_SESSIONS: dict[str, list[dict]] = {}
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

def _get_or_create_session(session_id: str | None) -> tuple[str, list[dict]]:
    if session_id and session_id in _SESSIONS:
        return session_id, _SESSIONS[session_id]
    sid = session_id or _DEFAULT_SESSION
    if sid not in _SESSIONS:
        _SESSIONS[sid] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        _inject_todos(_SESSIONS[sid])
    return sid, _SESSIONS[sid]

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
async def create_session():
    sid = str(uuid.uuid4())[:8]
    _SESSIONS[sid] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    _inject_todos(_SESSIONS[sid])
    return {"session_id": sid}

@router.post("/chat")
async def chat_endpoint(body: dict):
    user_message = body.get("message", "").strip()
    session_id = body.get("session_id")
    if not user_message:
        return {"error": "消息不能为空"}

    sid, messages = _get_or_create_session(session_id)
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

            done_data = {'prompt_tokens': usage['prompt_tokens'], 'completion_tokens': usage['completion_tokens'], 'session_id': sid}
            yield f"event: done\ndata: {json.dumps(done_data, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[Web] SSE 错误: {e}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=_DEFAULT_SESSION)):
    _, messages = _get_or_create_session(session_id)
    history = []
    for m in messages:
        if m["role"] == "system":
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
    session_id = (body or {}).get("session_id", _DEFAULT_SESSION)
    sid, messages = _get_or_create_session(session_id)
    system_content = messages[0]["content"]
    _SESSIONS[sid] = [{"role": "system", "content": system_content}]
    _inject_todos(_SESSIONS[sid])
    return {"status": "ok", "session_id": sid}
