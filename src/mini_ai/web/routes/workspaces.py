"""工作空间管理 API"""
from fastapi import APIRouter

from ...config import DATA_DIR, _raw, _config_path
from ...workspace import WorkspaceManager

router = APIRouter()

_ws_mgr = WorkspaceManager(DATA_DIR)


@router.get("/workspaces")
async def list_workspaces():
    workspaces = _ws_mgr.list_all()
    active = _raw.get("active_workspace", "default")
    return {"workspaces": workspaces, "active": active}


@router.post("/workspaces")
async def create_workspace(body: dict):
    name = body.get("name", "").strip()
    project_path = body.get("project_path", "").strip()
    if not name:
        return {"error": "名称不能为空"}
    result = _ws_mgr.create(name, project_path)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.post("/workspaces/add")
async def add_workspace(body: dict):
    path = body.get("path", "").strip()
    if not path:
        return {"error": "路径不能为空"}
    result = _ws_mgr.add(path)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}


@router.post("/workspaces/switch")
async def switch_workspace(body: dict):
    import yaml
    name = body.get("name", "").strip()
    if not name:
        return {"error": "名称不能为空"}
    ws = _ws_mgr.get(name)
    if not ws:
        return {"error": f"工作空间 '{name}' 不存在"}
    _raw["active_workspace"] = name
    _config_path.write_text(yaml.dump(_raw, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    return {"status": "ok", "message": f"已切换到 '{name}'，重启后生效"}


@router.delete("/workspaces/{name}")
async def remove_workspace(name: str, delete_data: bool = False):
    if delete_data:
        result = _ws_mgr.delete(name)
    else:
        result = _ws_mgr.remove(name)
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}
