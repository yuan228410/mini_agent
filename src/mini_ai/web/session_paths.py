"""Web session path resolution helpers."""
from __future__ import annotations

from pathlib import Path

from ..config import user_data_dir
from ..workspace import WorkspaceManager


def resolve_session_base(username: str, workspace: str | None) -> Path:
    """Resolve the sessions directory for a user's workspace."""

    workspace_name = workspace or "default"
    ws_base = get_workspace_session_base(username, workspace_name)
    if ws_base:
        return ws_base
    ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
    ws = ws_mgr.get(workspace_name)
    if ws:
        return _ensure_sessions_dir(ws.ws_dir)
    if workspace_name == "default":
        ws_mgr.create("default", str(Path.cwd()))
        ws = ws_mgr.get("default")
        if ws:
            return _ensure_sessions_dir(ws.ws_dir)
    raise ValueError(f"工作空间 '{workspace_name}' 不存在")


def get_workspace_session_base(username: str, workspace: str | None) -> Path | None:
    """Return an existing workspace's sessions directory, if available."""

    if not workspace:
        return None
    ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
    ws = ws_mgr.get(workspace)
    if ws:
        return _ensure_sessions_dir(ws.ws_dir)
    return None


def _ensure_sessions_dir(ws_dir: Path) -> Path:
    base = ws_dir / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base
