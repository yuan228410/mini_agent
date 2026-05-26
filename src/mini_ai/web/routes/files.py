"""文件浏览与预览 API"""
import os
from pathlib import Path

from fastapi import APIRouter, Query

from ...config import DATA_DIR, _raw
from ...workspace import WorkspaceManager

router = APIRouter()

_ws_mgr = WorkspaceManager(DATA_DIR)

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".vue": "vue", ".html": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".rs": "rust", ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".sql": "sql",
    ".xml": "xml", ".svg": "xml", ".dockerfile": "dockerfile",
    ".gitignore": "plaintext", ".env": "plaintext", ".txt": "plaintext",
}

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".tox", ".egg-info"}


def _get_project_root(workspace: str) -> Path | None:
    ws = _ws_mgr.get(workspace)
    if not ws:
        ws = _ws_mgr.get(_raw.get("active_workspace", "default"))
    if not ws or not ws.project_path:
        return None
    root = Path(ws.project_path)
    if root.exists() and root.is_dir():
        return root
    return None


def _safe_resolve(root: Path, rel_path: str) -> Path | None:
    if ".." in rel_path.split("/"):
        return None
    resolved = (root / rel_path).resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        return None
    return resolved


@router.get("/files/list")
async def list_files(
    path: str = Query(default=""),
    workspace: str = Query(default=""),
):
    root = _get_project_root(workspace)
    if not root:
        return {"error": "工作空间无关联项目路径"}

    root = root.resolve()
    target = _safe_resolve(root, path) if path else root
    if not target:
        return {"error": "路径不合法"}
    if not target.exists():
        return {"error": f"路径不存在: {path}"}
    if not target.is_dir():
        return {"error": "不是目录"}

    items = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return {"error": "权限不足"}

    for entry in entries:
        if entry.name in _IGNORE_DIRS:
            continue
        if entry.name.startswith(".") and entry.is_dir():
            continue

        rel = str(entry.relative_to(root))
        if entry.is_dir():
            items.append({"name": entry.name, "type": "dir", "path": rel})
        else:
            size = entry.stat().st_size
            lang = _EXT_LANG.get(entry.suffix.lower(), "")
            items.append({"name": entry.name, "type": "file", "path": rel, "size": size, "language": lang})

    breadcrumb = []
    if path:
        parts = Path(path).parts
        for i, part in enumerate(parts):
            breadcrumb.append({"name": part, "path": str(Path(*parts[:i+1]))})

    return {
        "root": str(root),
        "current_path": path,
        "breadcrumb": breadcrumb,
        "items": items,
    }


@router.get("/files/read")
async def read_file(
    path: str = Query(...),
    workspace: str = Query(default=""),
    offset: int = Query(default=0),
    limit: int = Query(default=200),
):
    root = _get_project_root(workspace)
    if not root:
        return {"error": "工作空间无关联项目路径"}

    target = _safe_resolve(root, path)
    if not target:
        return {"error": "路径不合法"}
    if not target.exists():
        return {"error": f"文件不存在: {path}"}
    if not target.is_file():
        return {"error": "不是文件"}

    size = target.stat().st_size
    if size > 5 * 1024 * 1024:
        return {"error": "文件过大（>5MB）", "size": size}

    lang = _EXT_LANG.get(target.suffix.lower(), "")

    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {"error": f"读取失败: {e}"}

    total_lines = len(lines)
    chunk = lines[offset:offset + limit]
    content = "".join(chunk)

    return {
        "path": path,
        "language": lang,
        "content": content,
        "offset": offset,
        "limit": limit,
        "total_lines": total_lines,
        "has_more": (offset + limit) < total_lines,
    }
