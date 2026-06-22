"""Workspace file path resolution helpers."""
from __future__ import annotations

from pathlib import Path


def project_root(workspace_manager, workspace: str, fallback_cwd: Path | None = None) -> Path | None:
    """Return a workspace project root with a local cwd fallback."""

    ws = workspace_manager.get(workspace)
    if not ws:
        ws = workspace_manager.get("default")
    if ws and ws.project_path:
        root = Path(ws.project_path)
        if root.exists() and root.is_dir():
            return root
    cwd = fallback_cwd or Path.cwd()
    if cwd.exists() and cwd.is_dir():
        return cwd
    return None


def safe_resolve(root: Path, rel_path: str) -> Path | None:
    """Resolve a relative path inside root, rejecting traversal."""

    if ".." in rel_path.split("/"):
        return None
    resolved = (root / rel_path).resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        return None
    return resolved
