"""聊天接口 — WebSocket 模式，多用户，HistoryDB 持久化，多会话并行"""
import asyncio
import json
import threading
import uuid
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ...config import DATA_DIR, MODEL_CONFIG, STREAMING, COMPACTOR, WEB, PLAN, RequestContext, get_model_config, user_data_dir
from ...llm import get_usage, reset_usage, chat as llm_chat
from ...runner import run_tool_loop
from ...tools import get_definitions, register_memory_tools, register_history_tools
from ...logger import logger
from ..display import WebDisplay

router = APIRouter()

_sessions_lock = threading.RLock()

# 三层缓存 key = f"{username}:{workspace}:{sid}"
# 所有缓存 dict 统一使用此 key 格式，与存储路径对齐


def _cache_key(username: str, workspace: str | None, sid: str) -> str:
    return f"{username}:{workspace or 'default'}:{sid}"


# 扁平结构，统一 key 格式
_SESSIONS: dict[str, list[dict]] = {}
_SESSION_MODELS: dict[str, str] = {}
_LAST_USAGE: dict[str, dict] = {}
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_STATUS: dict[str, str] = {}
_SESSION_ABORTS: dict[str, threading.Event] = {}
_META_CACHE: dict[str, dict] = {}
_SESSION_COMPONENTS: dict[str, dict] = {}
_SESSION_PLAN_MODE: dict[str, bool] = {}
# 注意：_SESSION_WORKSPACE 已删除，workspace 已编码在 key 中


def _get_workspace_base(username: str, workspace: str | None) -> Path | None:
    if not workspace:
        return None
    from ...workspace import WorkspaceManager
    ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
    ws = ws_mgr.get(workspace)
    if ws:
        base = ws.ws_dir / "web_sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return None


def _resolve_base(username: str, workspace: str | None) -> Path:
    """解析工作空间下的 web_sessions 目录。"""
    if not workspace:
        workspace = "default"
    ws_base = _get_workspace_base(username, workspace)
    if ws_base:
        return ws_base
    from ...workspace import WorkspaceManager
    ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
    ws = ws_mgr.get(workspace)
    if ws:
        base = ws.ws_dir / "web_sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base
    # 仅 default 不存在时自动创建
    if workspace == "default":
        ws_mgr.create("default", str(Path.cwd()))
        ws = ws_mgr.get("default")
        if ws:
            base = ws.ws_dir / "web_sessions"
            base.mkdir(parents=True, exist_ok=True)
            return base
    raise ValueError(f"工作空间 '{workspace}' 不存在")


def _inject_todos(messages: list[dict]):
    from ...tools import render_todos
    todos_text = render_todos()
    base = messages[0]["content"]
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[: base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"


def _get_session_lock(username: str, workspace: str | None, sid: str) -> threading.Lock:
    key = _cache_key(username, workspace, sid)
    with _sessions_lock:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = threading.Lock()
        return _SESSION_LOCKS[key]


def _lead_tool_defs() -> list[dict]:
    return [d for d in get_definitions() if d["function"]["name"] not in ("read_inbox", "list_teammates")]

def _get_or_create_components(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> dict:
    cache_key = _cache_key(username, workspace, sid)
    with _sessions_lock:
        if cache_key in _SESSION_COMPONENTS:
            return _SESSION_COMPONENTS[cache_key]

    from ...memory import MemoryStore, Compactor
    from ...memory.history_db import HistoryDB
    from ...context import ContextBuilder
    from ..deps import SKILL_LOADER

    user_memory_dir = user_data_dir(username) / "memory"

    if base is None:
        base = _resolve_base(username, workspace or "default")
    session_dir = base / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    session_memory_dir = session_dir / "memory_data"
    session_memory_dir.mkdir(parents=True, exist_ok=True)

    user_store = MemoryStore(user_memory_dir, episode_dir=session_memory_dir)

    history_db = HistoryDB(session_memory_dir / "history.db", workspace=workspace or "default")

    project_path = ""
    if workspace:
        from ...workspace import WorkspaceManager
        ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
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
        project_path=project_path,
    )

    components = {
        "store": user_store,
        "history_db": history_db,
        "compactor": compactor,
        "ctx_builder": ctx_builder,
        "project_path": project_path,
    }
    with _sessions_lock:
        if cache_key in _SESSION_COMPONENTS:
            return _SESSION_COMPONENTS[cache_key]
        _SESSION_COMPONENTS[cache_key] = components
    return components


def _build_system_prompt(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> str:
    _t0 = time.time()
    from ..deps import SKILL_LOADER
    if base is None:
        base = _resolve_base(username, workspace or "default")
    comp = _get_or_create_components(username, sid, base, workspace)
    result = comp["ctx_builder"].build(
        memory_store=comp["store"],
        skill_loader=SKILL_LOADER,
        project_path=comp["project_path"],
    )
    logger.debug(f"[perf] _build_system_prompt sid={sid} len={len(result)} time={time.time()-_t0:.3f}s")
    return result








def _parse_created_at(sid: str) -> str:
    try:
        dt = datetime.strptime(sid[:15], "%Y%m%d-%H%M%S")
        return dt.isoformat()
    except (ValueError, IndexError):
        return ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _build_meta(sid: str, messages: list[dict], username: str, workspace: str | None = None) -> dict:
    cache_key = _cache_key(username, workspace, sid)
    non_system = [m for m in messages if m["role"] != "system"]
    first_user = next((m.get("content", "")[:50] for m in non_system if m["role"] == "user"), "")
    name = messages[0].get("name", "") if messages else ""
    if not name:
        try:
            base = _resolve_base(username, workspace or "default")
            name = _load_session_name(base, sid)
        except Exception:
            pass
    if not name:
        name = first_user or "新会话"
    return {
        "session_id": sid,
        "name": name,
        "message_count": len(non_system),
        "preview": first_user,
        "created_at": _parse_created_at(sid),
        "updated_at": next((m.get("timestamp", "") for m in reversed(non_system) if m.get("timestamp")), _parse_created_at(sid)),
        "status": _SESSION_STATUS.get(cache_key, "idle"),
    }


def _update_meta_cache(username: str, sid: str, workspace: str | None = None, messages: list[dict] | None = None):
    cache_key = _cache_key(username, workspace, sid)
    if messages is not None:
        _META_CACHE[cache_key] = _build_meta(sid, messages, username, workspace)
    else:
        _META_CACHE.pop(cache_key, None)


def _save_session_name(base: Path | None, sid: str, name: str):
    if not base:
        return
    meta_path = base / sid / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta["name"] = name
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

def _load_session_name(base: Path | None, sid: str) -> str:
    if not base:
        return ""
    meta_path = base / sid / "meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("name", "")
        except Exception:
            pass
    return ""

_MAX_HISTORY_LOAD = 2000







def _ensure_user_sessions(username: str) -> dict[str, list[dict]]:
    # 兼容旧接口——_SESSIONS 已扁平化，此函数保持返回 dict 供外部，但不再使用
    return {}



def _load_from_db(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> list[dict] | None:
    _t0 = time.time()
    try:
        if base is None:
            base = _resolve_base(username, workspace or "default")
        comp = _get_or_create_components(username, sid, base, workspace)
        result = comp["history_db"].load_all(sid, limit=WEB.get("history_limit", 200))
        logger.debug(f"[perf] _load_from_db sid={sid} msgs={len(result) if result else 0} time={time.time()-_t0:.3f}s")
        return result
    except Exception as e:
        logger.error(f"[Web] _load_from_db error: {e}", exc_info=True)
        return None


def _rebuild_tool_messages(messages: list[dict]) -> list[dict]:
    has_tool_msgs = any(m.get("role") == "tool" for m in messages)
    result = []
    for m in messages:
        result.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tc_result = tc.get("_result", "")
                if has_tool_msgs and not tc_result:
                    continue
                tool_msg = {"role": "tool", "content": tc_result, "name": tc.get("function", {}).get("name", "")}
                if tc.get("id"):
                    tool_msg["tool_call_id"] = tc["id"]
                result.append(tool_msg)
    return result


def _get_or_create_session(username: str, session_id: str | None, base: Path | None = None, workspace: str | None = None, *, create: bool = True) -> tuple[str, list[dict] | None]:
    _t0 = time.time()
    with _sessions_lock:
        if not session_id:
            if not create:
                logger.warning(f"[session] create=False 但无 session_id，返回 None")
                return "", None
            session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        sid = session_id
        cache_key = _cache_key(username, workspace, sid)

        # 检查内存缓存
        if cache_key in _SESSIONS:
            existing = _SESSIONS[cache_key]
            if len(existing) > 1:
                return sid, existing

        # 尝试从数据库加载
        loaded = _load_from_db(username, sid, base, workspace)
        if loaded and len(loaded) > 1:
            if loaded[0].get("role") != "system":
                prompt = _build_system_prompt(username, sid, base, workspace)
                saved_name = _load_session_name(base, sid) or "新会话"
                loaded.insert(0, {"role": "system", "content": prompt, "name": saved_name, "timestamp": _now()})
            loaded = _rebuild_tool_messages(loaded)
            _SESSIONS[cache_key] = loaded
            _update_meta_cache(username, sid, workspace, loaded)
            return sid, loaded

        # 不存在且不允许创建
        if not create:
            logger.info(f"[session] 会话 '{sid}' 不存在且 create=False，返回 None")
            return sid, None

        # 全新会话
        logger.info(f"[session] 创建新会话 sid={sid} ws={workspace}")
        prompt = _build_system_prompt(username, sid, base, workspace)
        _SESSIONS[cache_key] = [{"role": "system", "content": prompt, "name": "新会话", "timestamp": _now()}]
        _update_meta_cache(username, sid, workspace, _SESSIONS[cache_key])
    logger.debug(f"[perf] _get_or_create_session sid={sid} ws={workspace} create={create} time={time.time()-_t0:.3f}s")
    return sid, _SESSIONS[cache_key]


def _run_tool_loop_sync(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                         messages: list[dict], tools: list[dict],
                         max_turns: int = 0, abort_event=None,
                         model_name=None, session_lock=None,
                         session_key: str = "",
                         username: str = "",
                         workspace: str | None = None) -> tuple:
    _SESSION_STATUS[session_key] = "generating"
    logger.debug(f"[Web] _run_tool_loop_sync start key={session_key} workspace={workspace}")
    try:
        from ...tools.update_todos import set_session
        set_session(session_key)

        base = _resolve_base(username, workspace)
        # session_key = f"{username}:{workspace}:{sid}", 取最后一段为 sid
        comp_key = session_key.split(":")[-1] if ":" in session_key else session_key
        comp = _get_or_create_components(username, comp_key, base, workspace)
        register_memory_tools(comp["store"])
        register_history_tools(comp["history_db"])

        if messages and messages[0]["role"] == "system" and len(messages[0]["content"]) < 50:
            messages[0]["content"] = _build_system_prompt(username, comp_key, base, workspace)

        disp = WebDisplay(queue, loop)
        plan_mode = _SESSION_PLAN_MODE.get(session_key, False)
        if plan_mode:
            tools = []
        cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
        ctx = RequestContext(model_config=cfg, display=disp)

        user_msgs = [m for m in messages if m["role"] == "user"]
        if user_msgs:
            last_user = user_msgs[-1]
            user_meta = {k: v for k, v in last_user.items() if k not in ("role", "content", "timestamp")}
            comp["history_db"].append("user", last_user.get("content", ""), session_id=comp_key, metadata=json.dumps(user_meta) if user_meta else "")

        if len(user_msgs) == 1 and messages[0].get("name", "") in ("", "新会话"):
            auto_name = user_msgs[0].get("content", "")[:50]
            if auto_name:
                messages[0]["name"] = auto_name
                _save_session_name(base, comp_key, auto_name)

        reset_usage()
        logger.debug(f"[Web] run_tool_loop start key={session_key} plan={plan_mode} tools={len(tools)}")
        _deferred_assistant = []

        def _persist(m):
            if m["role"] == "tool":
                comp["history_db"].append("tool", m.get("content", ""), session_id=comp_key, metadata=json.dumps({"name": m.get("name", ""), "tool_call_id": m.get("tool_call_id", "")}))
            elif m["role"] == "assistant":
                if m.get("tool_calls"):
                    _deferred_assistant.append(m)
                else:
                    asst_meta = {}
                    if m.get("thinking"):
                        asst_meta["thinking"] = m["thinking"]
                    comp["history_db"].append("assistant", m.get("content", ""), session_id=comp_key, metadata=json.dumps(asst_meta) if asst_meta else "")

        msg, _ = run_tool_loop(
            messages, tools,
            streaming=STREAMING,
            display=disp,
            inject_fn=_inject_todos,
            abort_event=abort_event,
            max_turns=max_turns,
            ctx=ctx,
            persist_fn=_persist,
        )
        logger.debug(f"[Web] run_tool_loop done key={session_key} msg={'yes' if msg else 'None'} content={len(msg.get('content') or '') if msg else 0}")
        tool_results_map = {m.get("name", ""): m.get("content", "") for m in messages if m.get("role") == "tool"}
        for am in _deferred_assistant:
            enriched_tcs = []
            for tc in am.get("tool_calls", []):
                tc_copy = {k: v for k, v in tc.items()}
                fn_name = tc.get("function", {}).get("name", "")
                if fn_name and fn_name in tool_results_map:
                    tc_copy["_result"] = tool_results_map[fn_name][:2000]
                enriched_tcs.append(tc_copy)
            am_meta = {}
            if am.get("thinking"):
                am_meta["thinking"] = am["thinking"]
            am_meta["tool_calls"] = enriched_tcs
            comp["history_db"].append("assistant", am.get("content") or "", session_id=comp_key, metadata=json.dumps(am_meta))
        if not msg or not msg.get("content"):
            err_text = "⚠ LLM 未返回有效回复（可能因限流或错误）"
            messages.append({"role": "assistant", "content": err_text, "timestamp": _now()})
            comp["history_db"].append("assistant", err_text, session_id=comp_key)
            loop.call_soon_threadsafe(lambda: queue.put_nowait({"event": "error", "data": {"error": err_text, "session_id": session_key}}))
        if msg and msg.get("content") and not any(
            m.get("role") == "assistant" and m.get("content") == msg["content"]
            for m in messages[-3:]
        ):
            if plan_mode:
                if PLAN.get("approval", True):
                    msg["content"] += "\n\n📋 以上为执行计划，确认后输入 /act 开始执行"
                else:
                    _SESSION_PLAN_MODE[session_key] = False
                    msg["content"] += "\n\n⚡ 已自动切换到执行模式，开始执行..."
            asst_ts = _now()
            messages.append({"role": "assistant", "content": msg["content"], "thinking": msg.get("thinking"), "timestamp": asst_ts})


        usage = get_usage()
        if comp["compactor"].should_compact(usage["prompt_tokens"]) or comp["compactor"].should_compact_local(messages):
            messages[:] = comp["compactor"].compact(llm_chat, messages, ctx=ctx)
            comp["history_db"].mark_archived()
            messages[0]["content"] = _build_system_prompt(username, comp_key, base, workspace)
            _inject_todos(messages)

        loop.call_soon_threadsafe(lambda: queue.put_nowait({"event": "complete", "data": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}}))
        return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}
    finally:
        _SESSION_STATUS[session_key] = "idle"


# ── Session CRUD ──

@router.post("/session")
async def create_session(body: dict):
    username = body.get("username", "")
    workspace = body.get("workspace") or "default"
    if not username:
        return {"error": "缺少 username"}
    base = _resolve_base(username, workspace)
    sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
    cache_key = _cache_key(username, workspace, sid)
    system_prompt = _build_system_prompt(username, sid, base, workspace)
    _SESSIONS[cache_key] = [{"role": "system", "content": system_prompt, "name": "新会话"}]
    _inject_todos(_SESSIONS[cache_key])
    _update_meta_cache(username, sid, workspace, _SESSIONS[cache_key])
    return {"session_id": sid}


@router.get("/sessions")
async def list_sessions(username: str = Query(...), workspace: str | None = Query(default=None)):
    _t0 = time.time()
    try:
        base = _resolve_base(username, workspace)
    except ValueError:
        return {"sessions": []}
    user_dir = base
    if not user_dir.exists():
        return {"sessions": []}
    sessions = []
    for d in sorted(user_dir.iterdir(), key=lambda d: d.name, reverse=True):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        sid = d.name
        cache_key = _cache_key(username, workspace, sid)
        cached = _META_CACHE.get(cache_key)
        if cached:
            cached["status"] = _SESSION_STATUS.get(cache_key, "idle")
            sessions.append(cached)
            continue
        msgs = _load_from_db(username, sid, base, workspace) or []
        meta = _build_meta(sid, msgs, username, workspace)
        _META_CACHE[cache_key] = meta
        # 跳过空会话（只有 system 消息，无实际对话）
        sessions.append(meta)
    if not sessions and (workspace is None or workspace == "default"):
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        cache_key = _cache_key(username, workspace, sid)
        system_prompt = _build_system_prompt(username, sid, base, workspace)
        _SESSIONS[cache_key] = [{"role": "system", "content": system_prompt, "name": "新会话", "timestamp": _now()}]
        _update_meta_cache(username, sid, workspace, _SESSIONS[cache_key])
        sessions.append(_build_meta(sid, _SESSIONS[cache_key], username, workspace))
        logger.info(f"[session] default 工作空间无会话，自动创建 sid={sid}")
    sessions.sort(key=lambda s: s.get("updated_at", "") or s.get("created_at", ""), reverse=True)
    logger.info(f"[perf] list_sessions ws={workspace} count={len(sessions)} time={time.time()-_t0:.3f}s")
    return {"sessions": sessions}


@router.delete("/session")
async def delete_session(body: dict):
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        return {"error": "缺少 session_id"}
    ws = workspace or None
    cache_key = _cache_key(username, ws, session_id)
    # 先清 HistoryDB，再删目录
    try:
        base = _resolve_base(username, ws)
        comp = _SESSION_COMPONENTS.get(cache_key)
        if comp:
            comp["history_db"].delete_by_session(session_id)
    except Exception:
        pass
    with _sessions_lock:
        _SESSIONS.pop(cache_key, None)
    _SESSION_COMPONENTS.pop(cache_key, None)
    _META_CACHE.pop(cache_key, None)
    _SESSION_STATUS.pop(cache_key, None)
    _SESSION_MODELS.pop(cache_key, None)
    _SESSION_LOCKS.pop(cache_key, None)
    from ...tools.update_todos import cleanup_session
    cleanup_session(cache_key)
    session_dir = _resolve_base(username, ws) / session_id
    if session_dir.exists():
        import shutil
        shutil.rmtree(session_dir, ignore_errors=True)
    return {"status": "ok"}


@router.post("/sessions/batch_delete")
async def batch_delete_sessions(body: dict):
    username = body.get("username", "")
    session_ids = body.get("session_ids", [])
    workspace = body.get("workspace", "") or "default"
    if not username or not session_ids:
        return {"error": "参数不完整", "deleted": 0}
    ws = workspace or None
    base = _resolve_base(username, ws)
    deleted = 0
    for sid in session_ids:
        cache_key = _cache_key(username, ws, sid)
        try:
            comp = _SESSION_COMPONENTS.get(cache_key)
            if comp:
                comp["history_db"].delete_by_session(sid)
        except Exception:
            pass
        with _sessions_lock:
            _SESSIONS.pop(cache_key, None)
        _SESSION_COMPONENTS.pop(cache_key, None)
        _META_CACHE.pop(cache_key, None)
        _SESSION_STATUS.pop(cache_key, None)
        _SESSION_MODELS.pop(cache_key, None)
        _SESSION_LOCKS.pop(cache_key, None)
        from ...tools.update_todos import cleanup_session
        cleanup_session(cache_key)
        session_dir = base / sid
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
            deleted += 1
    return {"status": "ok", "deleted": deleted}

@router.patch("/session/rename")
async def rename_session(body: dict):
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    name = body.get("name", "").strip()
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id or not name:
        return {"error": "参数不完整"}
    ws = workspace or None
    base = _resolve_base(username, ws)
    _, messages = _get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    messages[0]["name"] = name
    _update_meta_cache(username, session_id, ws, messages)
    _save_session_name(base, session_id, name)
    return {"status": "ok", "name": name}


# ── WebSocket endpoint (parallel) ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()
    # 前端连接时应在第一条消息中发送用户名，否则拒绝
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
                pass

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None):
        logger.info(f"[Web] WS _run_chat sid={sid} user={username} ws={ws_name}")
        session_key = _cache_key(username, ws_name, sid)
        base = _resolve_base(username, ws_name)
        messages = _get_or_create_session(username, sid, base, ws_name)[1]
        ts = _now()
        messages.append({"role": "user", "content": user_message, "timestamp": ts})
        _get_or_create_components(username, sid, base, ws_name)

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        abort_event = threading.Event()
        with _sessions_lock:
            _SESSION_ABORTS[session_key] = abort_event
        _ws_abort_keys.append(session_key)

        model_name = _SESSION_MODELS.get(session_key)
        s_lock = _get_session_lock(username, ws_name, sid)
        from ...config import RUNNER
        max_turns_web = RUNNER.get("max_turns", 20)
        future = loop.run_in_executor(
            None, _run_tool_loop_sync, queue, loop, messages, _lead_tool_defs(), max_turns_web, abort_event, model_name, s_lock, session_key, username, ws_name
        )

        complete_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        aborted = False
        try:
            while True:
                if abort_event.is_set():
                    aborted = True
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.15)
                    event["data"]["session_id"] = sid
                    if event["event"] == "complete" and "prompt_tokens" in event["data"]:
                        complete_usage = {"prompt_tokens": event["data"]["prompt_tokens"], "completion_tokens": event["data"].get("completion_tokens", 0)}
                    await _send(event)
                    if event["event"] in ("done", "aborted", "error", "complete"):
                        break
                except asyncio.TimeoutError:
                    if future.done():
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

        _LAST_USAGE[session_key] = usage

        _SESSION_ABORTS.pop(session_key, None)
        _update_meta_cache(username, sid, ws_name, messages)

        if not aborted:
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

                    if not _ws_username:
                        await _send({"event": "error", "data": {"error": "请先发送 login 消息"}})
                        continue

                    if msg_type == "abort":
                        abort_sid = data.get("session_id")
                        abort_username = _ws_username
                        abort_ws = data.get("workspace")
                        if abort_sid:
                            evt = _SESSION_ABORTS.get(_cache_key(abort_username, abort_ws, abort_sid))
                            if evt:
                                evt.set()
                        continue

                    if msg_type != "chat":
                        continue

                    user_message = data.get("message", "").strip()
                    username = _ws_username
                    session_id = data.get("session_id")
                    if not user_message:
                        await _send({"event": "error", "data": {"error": "消息不能为空"}})
                        continue

                    ws_name = data.get("workspace")
                    sid, _ = _get_or_create_session(username, session_id, workspace=ws_name)
                    task_key = _cache_key(username, ws_name, sid)
                    if task_key in _active_tasks:
                        await _send({"event": "error", "data": {"error": "该会话正在生成中", "session_id": sid}})
                        continue

                    task = asyncio.create_task(_run_chat(sid, username, user_message, ws_name))
                    _active_tasks[task_key] = task

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
    except Exception as e:
        pass
    finally:
        ws_closed = True
        for task in _active_tasks.values():
            task.cancel()
        reader_task.cancel()
        for sid_key in _ws_abort_keys:
            evt = _SESSION_ABORTS.pop(sid_key, None)
            if evt:
                evt.set()
        _ws_abort_keys.clear()
        _active_tasks.clear()



@router.get("/chat/search")
async def chat_search(keyword: str = Query(default=""), session_id: str = Query(default="default"),
                       username: str = Query(...), workspace: str = Query(default=""),
                       date_from: str = Query(default=""), date_to: str = Query(default=""),
                       limit: int = Query(default=20)):
    if not username:
        return {"results": []}
    base = _resolve_base(username, workspace or None)
    comp = _get_or_create_components(username, session_id, base, workspace or None)
    results = comp["history_db"].search(keyword, date_from=date_from, date_to=date_to, limit=limit)
    return {"results": results}

# ── History & Reset ──

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")):
    _t0 = time.time()
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    base = _resolve_base(username, workspace or None)
    cache_key = _cache_key(username, workspace or None, session_id)
    messages = _SESSIONS.get(cache_key)
    if not messages:
        messages = _load_from_db(username, session_id, base, workspace or None) or []
    if not messages:
        return {"session_id": session_id, "history": []}
    logger.info(f"[chat_history] sid={session_id} ws={workspace} base={base} msgs={len(messages)} time={time.time()-_t0:.3f}s")
    non_system = [m for m in messages if m["role"] not in ("system", "tool")]
    history = []
    for m in non_system:
        entry: dict = {"role": m["role"]}
        if m.get("content"):
            entry["content"] = m["content"]
        if m.get("timestamp"):
            entry["timestamp"] = m["timestamp"]
        if m.get("thinking"):
            entry["thinking"] = m["thinking"]
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        history.append(entry)
    return {"session_id": session_id, "history": history}


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
    base = _resolve_base(username, ws)
    sid, messages = _get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    system_content = messages[0]["content"]
    old_name = messages[0].get("name", "")
    cache_key = _cache_key(username, ws, sid)
    _SESSIONS[cache_key] = [{"role": "system", "content": system_content, "name": old_name or "新会话", "timestamp": _now()}]
    _inject_todos(_SESSIONS[cache_key])
    _update_meta_cache(username, sid, ws, _SESSIONS[cache_key])
    with _sessions_lock:
        _SESSION_COMPONENTS.pop(cache_key, None)
    return {"status": "ok", "session_id": sid}
