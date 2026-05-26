"""聊天接口 — WebSocket 模式，多用户，JSONL 持久化，多会话并行"""
import asyncio
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ...config import DATA_DIR, MODEL_CONFIG, STREAMING, COMPACTOR, RequestContext, get_model_config, user_data_dir
from ...llm import get_usage, chat as llm_chat
from ...runner import run_tool_loop
from ...tools import get_definitions, register_memory_tools, register_history_tools
from ...logger import logger
from ..display import WebDisplay

router = APIRouter()

_sessions_lock = threading.Lock()

_SYSTEM_PROMPT: str = ""


_SESSIONS: dict[str, dict[str, list[dict]]] = {}
_SESSION_MODELS: dict[str, str] = {}
_LAST_USAGE: dict[str, dict] = {}
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_STATUS: dict[str, str] = {}
_SESSION_ABORTS: dict[str, threading.Event] = {}
_META_CACHE: dict[str, dict] = {}
_SESSION_COMPONENTS: dict[str, dict] = {}
_SESSION_WORKSPACE: dict[str, str | None] = {}
_DEFAULT_SESSION = "default"


_USER_BASES: dict[str, Path] = {}

def switch_session_base(new_base, username: str = "default"):
    _USER_BASES[username] = new_base
    new_base.mkdir(parents=True, exist_ok=True)


def _get_workspace_base(username: str, workspace: str | None) -> Path | None:
    if not workspace:
        return None
    from ...workspace import WorkspaceManager
    ws_mgr = WorkspaceManager(user_data_dir(username))
    ws = ws_mgr.get(workspace)
    if ws:
        base = ws.ws_dir / "web_sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return None


def _resolve_base(username: str, workspace: str | None) -> Path:
    ws_name = workspace or "default"
    ws_base = _get_workspace_base(username, ws_name)
    if ws_base:
        return ws_base
    from ...workspace import WorkspaceManager
    ws_mgr = WorkspaceManager(user_data_dir(username))
    ws = ws_mgr.get("default")
    if ws:
        base = ws.ws_dir / "web_sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base
    base = user_data_dir(username) / "workspaces" / "default" / "web_sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base


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

def _get_or_create_components(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> dict:
    cache_key = f"{username}:{sid}"
    if cache_key in _SESSION_COMPONENTS:
        return _SESSION_COMPONENTS[cache_key]

    from ...memory import MemoryStore, Compactor
    from ...memory.history_db import HistoryDB
    from ...context import ContextBuilder
    from ..deps import SKILL_LOADER

    user_memory_dir = user_data_dir(username) / "memory"
    user_store = MemoryStore(user_memory_dir)

    sf = _session_file(username, sid, base)
    session_memory_dir = sf.parent / sid / "memory_data"
    session_memory_dir.mkdir(parents=True, exist_ok=True)

    history_db = HistoryDB(session_memory_dir / "history.db", workspace=sid[:15])

    project_path = ""
    if workspace:
        from ...workspace import WorkspaceManager
        ws_mgr = WorkspaceManager(user_data_dir(username))
        ws = ws_mgr.get(workspace)
        if ws:
            project_path = ws.project_path

    ctx_builder = ContextBuilder(DATA_DIR)
    compactor = Compactor(
        user_store,
        keep_recent=COMPACTOR.get("keep_recent", 50),
        char_threshold=COMPACTOR.get("char_threshold", 20000),
        context_usage_threshold=COMPACTOR.get("context_usage_threshold", 0.8),
        context_length=MODEL_CONFIG.get("context_length", 128000),
        context_builder=ctx_builder,
        skill_loader=SKILL_LOADER,
        history_db=history_db,
    )

    components = {
        "store": user_store,
        "history_db": history_db,
        "compactor": compactor,
        "ctx_builder": ctx_builder,
        "project_path": project_path,
    }
    _SESSION_COMPONENTS[cache_key] = components
    return components


def _build_system_prompt(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> str:
    from ..deps import SKILL_LOADER
    comp = _get_or_create_components(username, sid, base, workspace)
    return comp["ctx_builder"].build(
        memory_store=comp["store"],
        skill_loader=SKILL_LOADER,
        project_path=comp["project_path"],
    )




def set_system_prompt(prompt: str):
    global _SYSTEM_PROMPT
    _SYSTEM_PROMPT = prompt


def _user_dir(username: str, base: Path | None = None) -> Path:
    if base is None:
        base = _USER_BASES.get(username)
        if base is None:
            base = _resolve_base(username, None)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _session_file(username: str, session_id: str, base: Path | None = None) -> Path:
    return _user_dir(username, base) / f"{session_id}.jsonl"


def _parse_created_at(sid: str) -> str:
    try:
        dt = datetime.strptime(sid[:15], "%Y%m%d-%H%M%S")
        return dt.isoformat()
    except (ValueError, IndexError):
        return ""


def _build_meta(sid: str, messages: list[dict], username: str) -> dict:
    cache_key = f"{username}:{sid}"
    non_system = [m for m in messages if m["role"] != "system"]
    first_user = next((m.get("content", "")[:50] for m in non_system if m["role"] == "user"), "")
    name = messages[0].get("name", "") if messages else ""
    if not name:
        name = first_user or "新会话"
    return {
        "session_id": sid,
        "name": name,
        "message_count": len(non_system),
        "preview": first_user,
        "created_at": _parse_created_at(sid),
        "status": _SESSION_STATUS.get(cache_key, "idle"),
    }


def _update_meta_cache(username: str, sid: str, messages: list[dict] | None = None):
    cache_key = f"{username}:{sid}"
    if messages is not None:
        _META_CACHE[cache_key] = _build_meta(sid, messages, username)
    else:
        _META_CACHE.pop(cache_key, None)


_MAX_HISTORY_LOAD = 2000

def _load_from_file(username: str, session_id: str, base: Path | None = None) -> list[dict] | None:
    path = _session_file(username, session_id, base)
    if not path.exists():
        return None
    messages = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                    if len(messages) > _MAX_HISTORY_LOAD:
                        break
    except Exception:
        pass
    return messages if messages else None


def _append_to_file(username: str, session_id: str, msg: dict, base: Path | None = None):
    path = _session_file(username, session_id, base)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def _write_session_file(username: str, session_id: str, messages: list[dict], base: Path | None = None):
    path = _session_file(username, session_id, base)
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def _ensure_user_sessions(username: str) -> dict[str, list[dict]]:
    with _sessions_lock:
        if username not in _SESSIONS:
            _SESSIONS[username] = {}
        return _SESSIONS[username]


def _get_or_create_session(username: str, session_id: str | None, base: Path | None = None) -> tuple[str, list[dict]]:
    with _sessions_lock:
        if username not in _SESSIONS:
            _SESSIONS[username] = {}
        user_sessions = _SESSIONS[username]
        if session_id and session_id in user_sessions:
            existing = user_sessions[session_id]
            if len(existing) > 1:
                return session_id, existing
            loaded = _load_from_file(username, session_id, base)
            if loaded and len(loaded) > 1:
                user_sessions[session_id] = loaded
                _update_meta_cache(username, session_id, loaded)
                return session_id, loaded
            return session_id, existing
        sid = session_id or _DEFAULT_SESSION
        if sid in user_sessions:
            existing = user_sessions[sid]
            if len(existing) > 1:
                return sid, user_sessions[sid]
            loaded = _load_from_file(username, sid, base)
            if loaded and len(loaded) > 1:
                user_sessions[sid] = loaded
                _update_meta_cache(username, sid, loaded)
                return sid, user_sessions[sid]
            return sid, user_sessions[sid]
        loaded = _load_from_file(username, sid, base)
        if loaded:
            user_sessions[sid] = loaded
            _update_meta_cache(username, sid, loaded)
            return sid, user_sessions[sid]
        prompt = _build_system_prompt(username, sid, base) or _SYSTEM_PROMPT
        user_sessions[sid] = [{"role": "system", "content": prompt, "name": "新会话"}]
        _update_meta_cache(username, sid, user_sessions[sid])
    return sid, user_sessions[sid]


def _run_tool_loop_sync(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                         messages: list[dict], tools: list[dict],
                         max_turns: int = 30, abort_event=None,
                         model_name=None, session_lock=None,
                         session_key: str = "default",
                         username: str = "default",
                         workspace: str | None = None) -> tuple:
    _SESSION_STATUS[session_key] = "generating"
    try:
        from ...tools.update_todos import set_session
        set_session(session_key)

        base = _resolve_base(username, workspace)
        comp = _get_or_create_components(username, session_key.split(":")[-1] if ":" in session_key else session_key, base, workspace)
        register_memory_tools(comp["store"])
        register_history_tools(comp["history_db"])

        if messages and messages[0]["role"] == "system" and len(messages[0]["content"]) < 50:
            messages[0]["content"] = _build_system_prompt(username, session_key.split(":")[-1] if ":" in session_key else session_key, base, workspace)

        disp = WebDisplay(queue, loop)
        cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
        ctx = RequestContext(model_config=cfg, display=disp)

        user_msgs = [m for m in messages if m["role"] == "user"]
        if user_msgs:
            last_user = user_msgs[-1].get("content", "")
            comp["history_db"].append("user", last_user)

        msg, _ = run_tool_loop(
            messages, tools,
            streaming=STREAMING,
            display=disp,
            inject_fn=_inject_todos,
            abort_event=abort_event,
            max_turns=max_turns,
            ctx=ctx,
        )
        if msg and msg.get("content") and not any(
            m.get("role") == "assistant" and m.get("content") == msg["content"]
            for m in messages[-3:]
        ):
            messages.append({"role": "assistant", "content": msg["content"], "thinking": msg.get("thinking")})
            comp["history_db"].append("assistant", msg["content"])

        usage = get_usage()
        if comp["compactor"].should_compact(usage["prompt_tokens"]) or comp["compactor"].should_compact_local(messages):
            messages[:] = comp["compactor"].compact(llm_chat, messages, ctx=ctx)
            comp["history_db"].mark_archived()
            messages[0]["content"] = _build_system_prompt(username, session_key.split(":")[-1] if ":" in session_key else session_key, base, workspace)
            _inject_todos(messages)

        return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}
    finally:
        _SESSION_STATUS[session_key] = "idle"


# ── Session CRUD ──

@router.post("/session")
async def create_session(body: dict):
    username = body.get("username", "default")
    workspace = body.get("workspace")
    base = _resolve_base(username, workspace)
    sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
    user_sessions = _ensure_user_sessions(username)
    _SESSION_WORKSPACE[f"{username}:{sid}"] = workspace
    system_prompt = _build_system_prompt(username, sid, base, workspace) or _SYSTEM_PROMPT
    user_sessions[sid] = [{"role": "system", "content": system_prompt, "name": "新会话"}]
    _inject_todos(user_sessions[sid])
    _write_session_file(username, sid, user_sessions[sid], base)
    _update_meta_cache(username, sid, user_sessions[sid])
    return {"session_id": sid}


@router.get("/sessions")
async def list_sessions(username: str = Query(default="default"), workspace: str | None = Query(default=None)):
    base = _resolve_base(username, workspace)
    user_dir = _user_dir(username, base)
    if not user_dir.exists():
        return {"sessions": []}
    sessions = []
    for path in sorted(user_dir.glob("*.jsonl"), key=lambda f: f.name, reverse=True):
        sid = path.stem
        cache_key = f"{username}:{sid}"
        cached = _META_CACHE.get(cache_key)
        if cached and cached.get("message_count", 0) > 0:
            cached["status"] = _SESSION_STATUS.get(cache_key, "idle")
            sessions.append(cached)
            continue
        msgs = _load_from_file(username, sid, base) or []
        meta = _build_meta(sid, msgs, username)
        _META_CACHE[cache_key] = meta
        sessions.append(meta)
    return {"sessions": sessions}


@router.delete("/session")
async def delete_session(body: dict):
    username = body.get("username", "default")
    session_id = body.get("session_id", "")
    if not session_id or session_id == _DEFAULT_SESSION:
        return {"error": "无法删除默认会话"}
    with _sessions_lock:
        user_sessions = _SESSIONS.get(username, {})
        user_sessions.pop(session_id, None)
    path = _session_file(username, session_id)
    if path.exists():
        path.unlink()
    session_memory_dir = path.parent / session_id / "memory_data"
    if session_memory_dir.exists():
        import shutil
        shutil.rmtree(session_memory_dir, ignore_errors=True)
    _SESSION_COMPONENTS.pop(f"{username}:{session_id}", None)
    _update_meta_cache(username, session_id)
    _SESSION_STATUS.pop(f"{username}:{session_id}", None)
    return {"status": "ok"}


@router.patch("/session/rename")
async def rename_session(body: dict):
    username = body.get("username", "default")
    session_id = body.get("session_id", "")
    name = body.get("name", "").strip()
    if not session_id or not name:
        return {"error": "参数不完整"}
    _, messages = _get_or_create_session(username, session_id)
    messages[0]["name"] = name
    _write_session_file(username, session_id, messages)
    _update_meta_cache(username, session_id, messages)
    return {"status": "ok", "name": name}


# ── WebSocket endpoint (parallel) ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()

    _active_tasks: dict[str, asyncio.Task] = {}
    ws_closed = False
    _write_lock = asyncio.Lock()

    async def _send(data: dict):
        async with _write_lock:
            try:
                await ws.send_json(data)
            except Exception:
                pass

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None):
        logger.info(f"[Web] WS _run_chat sid={sid} user={username} ws={ws_name}")
        _SESSION_WORKSPACE[f"{username}:{sid}"] = ws_name
        messages = _get_or_create_session(username, sid)[1]
        messages.append({"role": "user", "content": user_message})
        _append_to_file(username, sid, {"role": "user", "content": user_message})

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        abort_event = threading.Event()
        session_key = f"{username}:{sid}"
        _SESSION_ABORTS[session_key] = abort_event

        model_name = _SESSION_MODELS.get(session_key)
        s_lock = _get_session_lock(username, sid)
        ws_name = _SESSION_WORKSPACE.get(session_key)
        future = loop.run_in_executor(
            None, _run_tool_loop_sync, queue, loop, messages, _lead_tool_defs(), 30, abort_event, model_name, s_lock, session_key, username, ws_name
        )

        aborted = False
        try:
            while True:
                if abort_event.is_set():
                    aborted = True
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.15)
                    event["data"]["session_id"] = sid
                    await _send(event)
                    if event["event"] in ("done", "aborted", "error"):
                        break
                except asyncio.TimeoutError:
                    if future.done():
                        break
        except Exception as e:
            logger.error(f"[Web] WS chat task error: {e}", exc_info=True)
            await _send({"event": "error", "data": {"error": str(e), "session_id": sid}})

        if aborted:
            await _send({"event": "aborted", "data": {"session_id": sid}})

        try:
            result = future.result(timeout=5)
            msg, usage = result if isinstance(result, tuple) else (result, {"prompt_tokens": 0, "completion_tokens": 0})
        except Exception:
            msg = None
            usage = {"prompt_tokens": 0, "completion_tokens": 0}

        _LAST_USAGE[session_key] = usage
        _write_session_file(username, sid, messages)

        _SESSION_ABORTS.pop(session_key, None)
        _update_meta_cache(username, sid, messages)

        if not aborted:
            await _send({
                "event": "done",
                "data": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"], "session_id": sid}
            })

        _active_tasks.pop(f"{username}:{sid}", None)

    async def _reader():
        nonlocal ws_closed
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

                    if msg_type == "abort":
                        abort_sid = data.get("session_id")
                        abort_username = data.get("username", "default")
                        if abort_sid:
                            evt = _SESSION_ABORTS.get(f"{abort_username}:{abort_sid}")
                            if evt:
                                evt.set()
                        continue

                    if msg_type != "chat":
                        continue

                    user_message = data.get("message", "").strip()
                    username = data.get("username", "default")
                    session_id = data.get("session_id")
                    if not user_message:
                        await _send({"event": "error", "data": {"error": "消息不能为空"}})
                        continue

                    sid, _ = _get_or_create_session(username, session_id)
                    if f"{username}:{sid}" in _active_tasks:
                        await _send({"event": "error", "data": {"error": "该会话正在生成中", "session_id": sid}})
                        continue

                    task = asyncio.create_task(_run_chat(sid, username, user_message, data.get("workspace")))
                    _active_tasks[f"{username}:{sid}"] = task

                except asyncio.TimeoutError:
                    pass
                except WebSocketDisconnect:
                    ws_closed = True
                    break
                except Exception as _e:
                    logger.error(f"[Web] WS reader error: {_e}")
                    ws_closed = True
                    break
        except Exception as _e:
            logger.error(f"[Web] WS outer error: {_e}")
        finally:
            if not ws_closed:
                ws_closed = True

    reader_task = asyncio.create_task(_reader())

    try:
        await reader_task
    except Exception:
        pass
    finally:
        ws_closed = True
        for task in _active_tasks.values():
            task.cancel()
        reader_task.cancel()
        for sid_key in list(_SESSION_ABORTS.keys()):
            _SESSION_ABORTS[sid_key].set()
        _active_tasks.clear()



@router.get("/chat/search")
async def chat_search(keyword: str = Query(default=""), session_id: str = Query(default=_DEFAULT_SESSION),
                       username: str = Query(default="default"), workspace: str = Query(default=""),
                       date_from: str = Query(default=""), date_to: str = Query(default=""),
                       limit: int = Query(default=20)):
    if not keyword:
        return {"results": []}
    base = _resolve_base(username, workspace or None)
    comp = _get_or_create_components(username, session_id, base, workspace or None)
    results = comp["history_db"].search(keyword, date_from=date_from, date_to=date_to, limit=limit)
    return {"results": results}

# ── History & Reset ──

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=_DEFAULT_SESSION), username: str = Query(default="default"), workspace: str = Query(default="")):
    base = _resolve_base(username, workspace or None)
    sid, messages = _get_or_create_session(username, session_id, base)
    logger.info(f"[chat_history] sid={sid} ws={workspace} base={base} msgs={len(messages)}")
    history = []
    tool_results: dict[str, str] = {}
    for m in messages:
        if m["role"] == "tool" and m.get("name"):
            tool_results[m["name"]] = m.get("content", "")

    for m in messages:
        if m["role"] in ("system", "tool"):
            continue
        entry: dict = {"role": m["role"]}
        if m.get("content"):
            entry["content"] = m["content"]
        if m.get("thinking"):
            entry["thinking"] = m["thinking"]
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
            for tc in entry["tool_calls"]:
                fn_name = tc.get("function", {}).get("name", "")
                if fn_name and fn_name in tool_results:
                    tc["_result"] = tool_results[fn_name]
        history.append(entry)
    return {"session_id": session_id, "history": history}


@router.post("/chat/reset")
async def chat_reset(body: dict | None = None):
    body = body or {}
    username = body.get("username", "default")
    session_id = body.get("session_id", _DEFAULT_SESSION)
    workspace = body.get("workspace", "")
    base = _resolve_base(username, workspace or None)
    sid, messages = _get_or_create_session(username, session_id, base)
    system_content = messages[0]["content"]
    old_name = messages[0].get("name", "")
    user_sessions = _ensure_user_sessions(username)
    user_sessions[sid] = [{"role": "system", "content": system_content, "name": old_name or "新会话"}]
    _inject_todos(user_sessions[sid])
    _write_session_file(username, sid, user_sessions[sid])
    _update_meta_cache(username, sid, user_sessions[sid])
    return {"status": "ok", "session_id": sid}
