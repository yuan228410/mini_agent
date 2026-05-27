"""文件浏览与预览 API"""
import os
from pathlib import Path

from fastapi import APIRouter, Query

from ...config import DATA_DIR, user_data_dir
from ...workspace import WorkspaceManager

router = APIRouter()

_ws_managers: dict[str, WorkspaceManager] = {}

def _get_ws_mgr(username: str) -> WorkspaceManager:
    if username not in _ws_managers:
        _ws_managers[username] = WorkspaceManager(user_data_dir(username))
    return _ws_managers[username]

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


_WEB_CWD = Path.cwd()

def _get_project_root(workspace: str, username: str = "default") -> Path | None:
    ws = _get_ws_mgr(username).get(workspace)
    if not ws:
        ws = _get_ws_mgr(username).get("default")
    if ws and ws.project_path:
        root = Path(ws.project_path)
        if root.exists() and root.is_dir():
            return root
    if _WEB_CWD.exists() and _WEB_CWD.is_dir():
        return _WEB_CWD
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
    username: str = Query(default="default"),
):
    root = _get_project_root(workspace, username)
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
    username: str = Query(default="default"),
    offset: int = Query(default=0),
    limit: int = Query(default=200),
):
    root = _get_project_root(workspace, username)
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


@router.get("/files/browse")
async def browse_dirs(path: str = Query(default=""), username: str = Query(default="default")):
    if not path:
        path = str(Path.home())
    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        return {"error": f"路径不存在: {path}", "current": path, "parent": "", "dirs": []}
    parent = str(root.parent) if root != root.parent else ""
    dirs = []
    try:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in ("__pycache__", "node_modules", ".venv", "venv", "dist", "build"):
                try:
                    has_children = any(e.is_dir() for e in entry.iterdir())
                except PermissionError:
                    has_children = False
                dirs.append({"name": entry.name, "path": str(entry), "has_children": has_children})
    except PermissionError:
        return {"error": "无权限访问", "current": path, "parent": parent, "dirs": []}
    return {"current": str(root), "parent": parent, "dirs": dirs}
