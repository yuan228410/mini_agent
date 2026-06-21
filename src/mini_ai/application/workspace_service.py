"""Workspace management use cases shared by UI adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..workspace import WorkspaceManager


@dataclass(frozen=True, slots=True)
class WorkspaceSwitchDependencies:
    clear_tool_cache: Callable[[], None]
    clear_workspace_sessions: Callable[[str], None]


def list_workspaces(mgr: WorkspaceManager, username: str, project_root: Path | None = None) -> dict[str, Any]:
    """Return workspaces and ensure the default workspace exists for a user."""

    root = project_root or Path.cwd()
    ws = mgr.get("default")
    default_project = root / username
    if ws and not ws.project_path:
        default_project.mkdir(parents=True, exist_ok=True)
        ws.update_project_path(str(default_project))
    elif not ws:
        default_project.mkdir(parents=True, exist_ok=True)
        mgr.create("default", str(default_project))
    return {"workspaces": mgr.list_all(), "active": "default"}


def create_workspace(mgr: WorkspaceManager, name: str, project_path: str, username: str) -> dict[str, Any]:
    """Create a named workspace for a user."""

    if not name:
        return {"error": "名称不能为空"}
    if not username:
        return {"error": "缺少 username"}
    result = mgr.create(name, project_path)
    return _action_response(result)


def add_workspace(mgr: WorkspaceManager, path: str, username: str) -> dict[str, Any]:
    """Add an existing project directory as a workspace."""

    if not path:
        return {"error": "路径不能为空"}
    if not username:
        return {"error": "缺少 username"}
    result = mgr.add(path)
    return _action_response(result)


def switch_workspace(
    mgr: WorkspaceManager,
    name: str,
    username: str,
    deps: WorkspaceSwitchDependencies | None = None,
) -> dict[str, Any]:
    """Validate workspace switching and run adapter-provided invalidation hooks."""

    if not name:
        return {"error": "名称不能为空"}
    if not username:
        return {"error": "缺少 username"}
    ws = mgr.get(name)
    if not ws:
        return {"error": f"工作空间 '{name}' 不存在"}
    if deps:
        deps.clear_tool_cache()
        deps.clear_workspace_sessions(f"{username}:{name}:")
    return {"status": "ok", "message": f"已切换到 '{name}'", "project_path": ws.project_path}


def remove_workspace(mgr: WorkspaceManager, name: str, delete_data: bool = False) -> dict[str, Any]:
    """Remove a workspace, optionally deleting all data."""

    result = mgr.delete(name) if delete_data else mgr.remove(name)
    return _action_response(result)


def list_removed_workspaces(mgr: WorkspaceManager) -> dict[str, Any]:
    """Return removed workspace backups."""

    return {"removed": mgr.list_removed()}


def restore_workspace(mgr: WorkspaceManager, name: str, username: str) -> dict[str, Any]:
    """Restore a previously removed workspace."""

    if not name:
        return {"error": "名称不能为空"}
    if not username:
        return {"error": "缺少 username"}
    return _action_response(mgr.restore(name))


def delete_removed_workspace(mgr: WorkspaceManager, name: str) -> dict[str, Any]:
    """Permanently delete removed workspace backups."""

    return _action_response(mgr.delete_removed(name))


def _action_response(result: str) -> dict[str, Any]:
    if result.startswith("Error"):
        return {"error": result}
    return {"status": "ok", "message": result}
