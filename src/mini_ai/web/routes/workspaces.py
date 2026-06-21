"""工作空间管理 API — 按用户隔离"""
from fastapi import APIRouter, Query

import time

from ...application import workspace_service
from ...logger import logger
from ..runtime_helpers import cached_workspace_manager_for_user, workspace_switch_dependencies
from ..route_types import (
    RemovedWorkspacesResponse,
    RouteErrorResponse,
    WorkspaceActionResponse,
    WorkspaceAddRequest,
    WorkspaceCreateRequest,
    WorkspaceListResponse,
    WorkspaceRestoreRequest,
    WorkspaceSwitchRequest,
    WorkspaceSwitchResponse,
)

router = APIRouter()


def _get_mgr(username: str):
    return cached_workspace_manager_for_user(username)


@router.get("/workspaces")
async def list_workspaces(username: str = Query(...)) -> WorkspaceListResponse:
    _t0 = time.time()
    result = workspace_service.list_workspaces(_get_mgr(username), username)
    logger.debug(f"[perf] list_workspaces user={username} count={len(result['workspaces'])} time={time.time()-_t0:.3f}s")
    return result


@router.post("/workspaces")
async def create_workspace(body: WorkspaceCreateRequest) -> WorkspaceActionResponse | RouteErrorResponse:
    name = body.get("name", "").strip()
    project_path = body.get("project_path", "").strip()
    username = body.get("username", "")
    return workspace_service.create_workspace(_get_mgr(username), name, project_path, username)


@router.post("/workspaces/add")
async def add_workspace(body: WorkspaceAddRequest) -> WorkspaceActionResponse | RouteErrorResponse:
    path = body.get("path", "").strip()
    username = body.get("username", "")
    return workspace_service.add_workspace(_get_mgr(username), path, username)


@router.post("/workspaces/switch")
async def switch_workspace(body: WorkspaceSwitchRequest) -> WorkspaceSwitchResponse | RouteErrorResponse:
    name = body.get("name", "").strip()
    username = body.get("username", "")
    return workspace_service.switch_workspace(
        _get_mgr(username),
        name,
        username,
        workspace_switch_dependencies(),
    )


@router.delete("/workspaces/{name}")
async def remove_workspace(name: str, delete_data: bool = False, username: str = Query(...)) -> WorkspaceActionResponse | RouteErrorResponse:
    return workspace_service.remove_workspace(_get_mgr(username), name, delete_data)


@router.get("/workspaces/removed")
async def list_removed_workspaces(username: str = Query(...)) -> RemovedWorkspacesResponse:
    return workspace_service.list_removed_workspaces(_get_mgr(username))


@router.post("/workspaces/restore")
async def restore_workspace(body: WorkspaceRestoreRequest) -> WorkspaceActionResponse | RouteErrorResponse:
    name = body.get("name", "").strip()
    username = body.get("username", "")
    return workspace_service.restore_workspace(_get_mgr(username), name, username)


@router.delete("/workspaces/removed/{name}")
async def delete_removed_workspace(name: str, username: str = Query(...)) -> WorkspaceActionResponse | RouteErrorResponse:
    return workspace_service.delete_removed_workspace(_get_mgr(username), name)
