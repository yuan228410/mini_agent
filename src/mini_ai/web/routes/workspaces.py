"""工作空间管理 API — 按用户隔离"""
from fastapi import APIRouter

from ...config import DATA_DIR, user_data_dir
from ...workspace import WorkspaceManager

router = APIRouter()

_ws_managers: dict[str, WorkspaceManager] = {}


def _get_mgr(username: str) -> WorkspaceManager:
    if username not in _ws_managers:
        _ws_managers[username] = WorkspaceManager(user_data_dir(username))
    return _ws_managers[username]


@router.get("/workspaces")
async def list_workspaces(username: str = ""):
    import os
    mgr = _get_mgr(username or "default")
    ws = mgr.get("default")
    if ws and not ws.project_path:
        ws.update_project_path(os.getcwd())
    workspaces = mgr.list_all()
    return {"workspaces": workspaces, "active": "default"}


@router.post("/workspaces")
async def create_workspace(body: dict):
    name = body.get("name", "").strip()
    project_path = body.get("project_path", "").strip()
    username = body.get("username", "default")
    if not name:
        return {"error": "名称不能为空"}
    mgr = _get_mgr(username)
    result = mgr.create(name, project_path)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.post("/workspaces/add")
async def add_workspace(body: dict):
    path = body.get("path", "").strip()
    username = body.get("username", "default")
    if not path:
        return {"error": "路径不能为空"}
    mgr = _get_mgr(username)
    result = mgr.add(path)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.post("/workspaces/switch")
async def switch_workspace(body: dict):
    name = body.get("name", "").strip()
    username = body.get("username", "default")
    if not name:
        return {"error": "名称不能为空"}
    mgr = _get_mgr(username)
    ws = mgr.get(name)
    if not ws:
        return {"error": f"工作空间 '{name}' 不存在"}

    from .chat import switch_session_base, set_system_prompt
    from ...context import ContextBuilder
    from ...memory import MemoryStore
    from ...skills import SkillLoader

    switch_session_base(ws.ws_dir / "web_sessions", username)

    store = MemoryStore(ws.ws_dir / "memory_data")
    ctx = ContextBuilder(DATA_DIR)
    skill_loader = SkillLoader(DATA_DIR / "skills", [])
    system_prompt = ctx.build(memory_store=store, skill_loader=skill_loader, project_path=ws.project_path)
    set_system_prompt(system_prompt)

    from .chat import _user_dir, _DEFAULT_SESSION
    user_dir = _user_dir(username)
    files = list(user_dir.glob("*.jsonl")) if user_dir.exists() else []
    latest_sid = max(files, key=lambda f: f.name).stem if files else _DEFAULT_SESSION

    return {"status": "ok", "message": f"已切换到 '{name}'", "session_id": latest_sid, "project_path": ws.project_path}


@router.delete("/workspaces/{name}")
async def remove_workspace(name: str, delete_data: bool = False, username: str = ""):
    mgr = _get_mgr(username or "default")
    if delete_data:
        result = mgr.delete(name)
    else:
        result = mgr.remove(name)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}
