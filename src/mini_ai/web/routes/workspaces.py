"""工作空间管理 API — 按用户隔离"""
from fastapi import APIRouter, Query

import time

from ...config import DATA_DIR, user_data_dir
from ...logger import logger
from ...workspace import WorkspaceManager

router = APIRouter()

_ws_managers: dict[str, WorkspaceManager] = {}


def _get_mgr(username: str) -> WorkspaceManager:
    if username not in _ws_managers:
        _ws_managers[username] = WorkspaceManager(user_data_dir(username), ensure_default=False)
    return _ws_managers[username]


@router.get("/workspaces")
async def list_workspaces(username: str = Query(...)):
    _t0 = time.time()
    import os
    mgr = _get_mgr(username)
    ws = mgr.get("default")
    if ws and not ws.project_path:
        user_dir = os.path.join(os.getcwd(), username)
        os.makedirs(user_dir, exist_ok=True)
        ws.update_project_path(user_dir)
    elif not ws:
        mgr.create("default", os.path.join(os.getcwd(), username))
    workspaces = mgr.list_all()
    logger.info(f"[perf] list_workspaces user={username} count={len(workspaces)} time={time.time()-_t0:.3f}s")
    return {"workspaces": workspaces, "active": "default"}


@router.post("/workspaces")
async def create_workspace(body: dict):
    name = body.get("name", "").strip()
    project_path = body.get("project_path", "").strip()
    username = body.get("username", "")
    if not name:
        return {"error": "名称不能为空"}
    if not username:
        return {"error": "缺少 username"}
    mgr = _get_mgr(username)
    result = mgr.create(name, project_path)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.post("/workspaces/add")
async def add_workspace(body: dict):
    path = body.get("path", "").strip()
    username = body.get("username", "")
    if not path:
        return {"error": "路径不能为空"}
    if not username:
        return {"error": "缺少 username"}
    mgr = _get_mgr(username)
    result = mgr.add(path)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.post("/workspaces/switch")
async def switch_workspace(body: dict):
    name = body.get("name", "").strip()
    username = body.get("username", "")
    if not name:
        return {"error": "名称不能为空"}
    if not username:
        return {"error": "缺少 username"}
    mgr = _get_mgr(username)
    ws = mgr.get(name)
    if not ws:
        return {"error": f"工作空间 '{name}' 不存在"}

    from .chat import _SESSION_COMPONENTS
    from ...context import ContextBuilder
    from ...memory import MemoryStore
    from ...skills import SkillLoader

    # 清理目标工作空间的缓存，不波及其他工作空间
    import threading
    from .chat import _sessions_lock, _SESSIONS, _SESSION_LOCKS, _SESSION_MODELS, _SESSION_STATUS, _SESSION_PLAN_MODE, _META_CACHE
    with _sessions_lock:
        prefix = f"{username}:{name}:"
        for d in (_SESSION_COMPONENTS, _SESSION_LOCKS, _SESSION_MODELS, _SESSION_STATUS, _SESSION_PLAN_MODE, _META_CACHE, _SESSIONS):
            if isinstance(d, dict):
                keys = [k for k in d if k.startswith(prefix)]
                for k in keys:
                    del d[k]

    store = MemoryStore(ws.ws_dir / "memory_data")
    ctx = ContextBuilder(DATA_DIR)
    skill_loader = SkillLoader(DATA_DIR / "skills", [])
    system_prompt = ctx.build(memory_store=store, skill_loader=skill_loader, project_path=ws.project_path)

    return {"status": "ok", "message": f"已切换到 '{name}'", "project_path": ws.project_path}


@router.delete("/workspaces/{name}")
async def remove_workspace(name: str, delete_data: bool = False, username: str = Query(...)):
    mgr = _get_mgr(username)
    if delete_data:
        result = mgr.delete(name)
    else:
        result = mgr.remove(name)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.get("/workspaces/removed")
async def list_removed_workspaces(username: str = Query(...)):
    mgr = _get_mgr(username)
    removed = mgr.list_removed()
    return {"removed": removed}


@router.post("/workspaces/restore")
async def restore_workspace(body: dict):
    name = body.get("name", "").strip()
    username = body.get("username", "")
    if not name:
        return {"error": "名称不能为空"}
    if not username:
        return {"error": "缺少 username"}
    mgr = _get_mgr(username)
    result = mgr.restore(name)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.delete("/workspaces/removed/{name}")
async def delete_removed_workspace(name: str, username: str = Query(...)):
    mgr = _get_mgr(username)
    result = mgr.delete_removed(name)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}
