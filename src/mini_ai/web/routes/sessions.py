"""会话 REST API — CRUD + 列表 + 重命名 + 待办"""
import json
import uuid
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query

from ...config import MODEL_CONFIG, RequestContext, get_model_config
from ...core.runtime_types import ACTIVE_TEAM_MEMBER_STATUSES
from ...llm import chat as llm_chat
from ...logger import logger
from ...tools import inject_todos as _inject_todos
from ...utils import now_ts
from ..route_types import (
    RouteErrorResponse,
    RouteOkResponse,
    SessionBatchDeleteRequest,
    SessionBatchDeleteResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDeleteRequest,
    SessionListResponse,
    SessionRenameRequest,
    SessionRenameResponse,
    TodosResponse,
)
from ..session_manager import (
    SessionManager, cache_key, ws_key,
    resolve_base, get_or_create_session, get_or_create_components,
    build_system_prompt, _load_session_name, _save_session_name,
    _load_session_model, _save_session_model,
    _update_meta_cache, _build_meta, _load_from_db,
)

router = APIRouter()


@router.post("/session")
async def create_session(body: SessionCreateRequest) -> SessionCreateResponse | RouteErrorResponse:
    username = body.get("username", "")
    workspace = body.get("workspace") or "default"
    if not username:
        return {"error": "缺少 username"}
    logger.info(f"[session] 用户主动创建会话 ws={workspace}")
    base = resolve_base(username, workspace)
    sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
    key = cache_key(username, workspace, sid)
    system_prompt = build_system_prompt(username, sid, base, workspace)

    sm = SessionManager.instance()
    sm.create_session(key, [{"role": "system", "content": system_prompt, "name": "新会话"}])

    from ...tools.update_todos import set_session
    set_session(key)
    msgs = sm.get_messages(key)
    if msgs:
        _inject_todos(msgs)
    _update_meta_cache(username, sid, workspace, msgs)

    comp = get_or_create_components(username, sid, base, workspace)
    comp["history_db"].append(workspace, sid, "system", system_prompt, metadata=json.dumps({"name": "新会话"}))

    return {"session_id": sid}


@router.get("/sessions")
async def list_sessions(username: str = Query(...), workspace: str | None = Query(default=None)) -> SessionListResponse:
    _t0 = time.time()
    try:
        base = resolve_base(username, workspace)
    except ValueError:
        return {"sessions": []}
    if not base.exists():
        return {"sessions": []}

    sm = SessionManager.instance()
    sessions = []
    for d in sorted(base.iterdir(), key=lambda d: d.name, reverse=True):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        sid = d.name
        key = cache_key(username, workspace, sid)
        cached = sm.get_meta(key)
        if cached:
            cached = dict(cached)
            cached["status"] = sm.get_status(key)
            sessions.append(cached)
            continue
        msgs = _load_from_db(username, sid, base, workspace) or []
        meta = _build_meta(sid, msgs, username, workspace)
        sm.set_meta(key, meta)
        sessions.append(meta)

    if not sessions and (workspace is None or workspace == "default"):
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        key = cache_key(username, workspace, sid)
        system_prompt = build_system_prompt(username, sid, base, workspace)
        sm.create_session(key, [{"role": "system", "content": system_prompt, "name": "新会话"}])
        _update_meta_cache(username, sid, workspace, sm.get_messages(key))
        comp = get_or_create_components(username, sid, base, workspace)
        comp["history_db"].append(workspace or "default", sid, "system", system_prompt, metadata=json.dumps({"name": "新会话"}))
        sessions.append(_build_meta(sid, sm.get_messages(key), username, workspace))
        logger.info(f"[session] default 工作空间无会话，自动创建 sid={sid}")

    sessions.sort(key=lambda s: s.get("updated_at", "") or s.get("created_at", ""), reverse=True)
    logger.debug(f"[perf] list_sessions ws={workspace} count={len(sessions)} time={time.time()-_t0:.3f}s")
    return {"sessions": sessions}


@router.get("/todos")
async def get_todos(username: str = Query(...), workspace: str | None = Query(default=None), session_id: str = Query(...)) -> TodosResponse:
    from ...tools.update_todos import get_todos as _get_todos
    key = cache_key(username, workspace, session_id)
    todos = _get_todos(key)
    return {"todos": todos}


@router.delete("/session")
async def delete_session(body: SessionDeleteRequest) -> RouteOkResponse | RouteErrorResponse:
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        return {"error": "缺少 session_id"}
    ws = workspace or None
    key = cache_key(username, ws, session_id)

    try:
        base = resolve_base(username, ws)
        comp = get_or_create_components(username, session_id, base, ws)
        comp["history_db"].delete_session(ws or "default", session_id)
    except Exception:
        pass

    sm = SessionManager.instance()
    sm.delete_session(key)

    wk = ws_key(username, ws)
    remaining = any(sm.get_messages(k) is not None for k in sm._sessions if k.startswith(f"{username}:{ws or 'default'}:") and k != key)
    if not remaining:
        team_comp = sm.get_team_component(wk)
        if team_comp:
            team_mgr = team_comp.get("team_mgr")
            if team_mgr:
                for m in team_mgr.config.get("members", []):
                    if m.get("status") in ACTIVE_TEAM_MEMBER_STATUSES:
                        team_comp["bus"].send("lead", m["name"], "会话结束，请退出。", "shutdown_request")

    from ...tools.update_todos import cleanup_session
    cleanup_session(key)
    session_dir = resolve_base(username, ws) / session_id
    if session_dir.exists():
        import shutil
        shutil.rmtree(session_dir, ignore_errors=True)
    return {"status": "ok"}


@router.post("/sessions/batch_delete")
async def batch_delete_sessions(body: SessionBatchDeleteRequest) -> SessionBatchDeleteResponse | RouteErrorResponse:
    username = body.get("username", "")
    session_ids = body.get("session_ids", [])
    workspace = body.get("workspace", "") or "default"
    if not username or not session_ids:
        return {"error": "参数不完整", "deleted": 0}
    ws = workspace or None
    base = resolve_base(username, ws)
    sm = SessionManager.instance()
    deleted = 0

    for sid in session_ids:
        key = cache_key(username, ws, sid)
        try:
            comp = get_or_create_components(username, sid, base, ws)
            comp["history_db"].delete_session(workspace or "default", sid)
        except Exception:
            pass
        sm.delete_session(key)
        from ...tools.update_todos import cleanup_session
        cleanup_session(key)
        session_dir = base / sid
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
        deleted += 1
    return {"status": "ok", "deleted": deleted}


@router.patch("/session/rename")
async def rename_session(body: SessionRenameRequest) -> SessionRenameResponse | RouteErrorResponse:
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    name = body.get("name", "").strip()
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id or not name:
        return {"error": "参数不完整"}
    ws = workspace or None
    base = resolve_base(username, ws)
    _, messages = get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    messages[0]["name"] = name
    _update_meta_cache(username, session_id, ws, messages)
    _save_session_name(base, session_id, name)
    return {"status": "ok", "name": name}
