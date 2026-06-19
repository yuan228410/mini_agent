"""文件浏览与预览 API"""
import asyncio
import os
import time
from datetime import datetime
from ...utils import _UTC8
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from ...config import DATA_DIR, user_data_dir
from ...workspace import WorkspaceManager
from ..route_types import (
    BrowseDirsResponse,
    FileListResponse,
    FileReadBinaryResponse,
    FileReadTextResponse,
    FileSearchResponse,
    RouteErrorResponse,
)

router = APIRouter()

def _get_ws_mgr(username: str) -> WorkspaceManager:
    from .workspaces import _get_mgr
    return _get_mgr(username)

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".vue": "vue", ".html": "html", ".css": "css", ".scss": "scss",
    ".less": "less", ".sass": "sass",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".rs": "rust", ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".sql": "sql",
    ".xml": "xml", ".svg": "xml", ".dockerfile": "dockerfile",
    ".gitignore": "plaintext", ".env": "plaintext", ".txt": "plaintext",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala",
    ".r": "r", ".lua": "lua", ".dart": "dart",
    ".erl": "erlang", ".ex": "elixir", ".exs": "elixir",
    ".clj": "clojure", ".groovy": "groovy",
    ".tf": "hcl", ".proto": "protobuf",
    ".conf": "ini", ".ini": "ini", ".cfg": "ini",
    ".cmake": "cmake", ".patch": "diff", ".diff": "diff",
    ".prisma": "prisma", ".elm": "elm",
    ".tex": "latex", ".sty": "tex",
    ".graphql": "graphql", ".gql": "graphql",
    ".vim": "vim",
    ".lock": "plaintext", ".dockerignore": "plaintext",
    ".editorconfig": "ini", ".prettierrc": "json",
}

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".ogg",
    ".ttf", ".woff", ".woff2", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".class", ".o", ".obj", ".a",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".ipynb",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg"}

_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".webp": "image/webp", ".svg": "image/svg+xml",
}

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".tox", ".egg-info"}

_WEB_CWD = Path.cwd()

def _get_project_root(workspace: str, username: str) -> Path | None:
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

def _is_binary_file(filepath: Path, ext: str) -> bool:
    """检测文件是否为二进制。先查扩展名黑名单，再采样内容。"""
    if ext in _BINARY_EXTENSIONS:
        return True
    if ext in _IMAGE_EXTENSIONS:
        return True
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
        if b"\x00" in chunk:
            return True
        non_printable = sum(1 for b in chunk if b < 0x20 and b not in (0x09, 0x0A, 0x0D))
        return non_printable > len(chunk) * 0.3
    except Exception:
        return False

def _format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_UTC8).isoformat()


def _list_files_sync(root: Path, target: Path, path: str, max_items: int = 2000) -> FileListResponse | RouteErrorResponse:
    items = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return {"error": "权限不足"}

    truncated = False
    for entry in entries:
        if len(items) >= max_items:
            truncated = True
            break
        try:
            if entry.name in _IGNORE_DIRS:
                continue
            if entry.name.startswith(".") and entry.is_dir():
                continue
            rel = str(entry.relative_to(root))
            if entry.is_dir():
                items.append({"name": entry.name, "type": "dir", "path": rel})
            else:
                st = entry.stat()
                lang = _EXT_LANG.get(entry.suffix.lower(), "")
                items.append({
                    "name": entry.name, "type": "file", "path": rel,
                    "size": st.st_size, "language": lang,
                    "modified": _format_time(st.st_mtime),
                })
        except (OSError, PermissionError):
            continue

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
        "truncated": truncated,
    }


def _read_text_window(target: Path, offset: int, limit: int) -> tuple[str, int, bool]:
    content_parts = []
    total_lines = 0
    end = offset + limit
    with target.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if offset <= total_lines < end:
                content_parts.append(line)
            total_lines += 1
    return "".join(content_parts), total_lines, end < total_lines


def _search_files_sync(root: Path, target: Path, query: str, max_results: int = 100, max_scanned: int = 20000, deadline_ms: int = 1500) -> FileSearchResponse:
    results = []
    query_lower = query.lower()
    scanned = 0
    truncated = False
    deadline = time.monotonic() + deadline_ms / 1000
    stack = [target]

    while stack:
        if scanned >= max_scanned or time.monotonic() > deadline:
            truncated = True
            break
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            scanned += 1
            if scanned >= max_scanned or time.monotonic() > deadline:
                truncated = True
                break
            try:
                if entry.name in _IGNORE_DIRS:
                    continue
                is_dir = entry.is_dir()
                if is_dir and entry.name.startswith("."):
                    continue
                if is_dir:
                    stack.append(entry)
                if query_lower in entry.name.lower() and len(results) < max_results:
                    rel = str(entry.relative_to(root))
                    if is_dir:
                        results.append({"name": entry.name, "type": "dir", "path": rel})
                    else:
                        st = entry.stat()
                        lang = _EXT_LANG.get(entry.suffix.lower(), "")
                        results.append({
                            "name": entry.name, "type": "file", "path": rel,
                            "size": st.st_size, "language": lang,
                            "modified": _format_time(st.st_mtime),
                        })
                elif len(results) >= max_results:
                    truncated = True
                    break
            except (OSError, PermissionError):
                continue
    results.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {"results": results, "query": query, "scanned": scanned, "truncated": truncated}


def _browse_dirs_sync(root: Path) -> BrowseDirsResponse | RouteErrorResponse:
    parent = str(root.parent) if root != root.parent else ""
    dirs = []
    try:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in _IGNORE_DIRS:
                try:
                    has_children = any(e.is_dir() for e in entry.iterdir())
                except (OSError, PermissionError):
                    has_children = False
                dirs.append({"name": entry.name, "path": str(entry), "has_children": has_children})
    except PermissionError:
        return {"error": "无权限访问", "current": str(root), "parent": parent, "dirs": []}
    return {"current": str(root), "parent": parent, "dirs": dirs}

@router.get("/files/list")
async def list_files(
    path: str = Query(default=""),
    workspace: str = Query(default=""),
    username: str = Query(...),
) -> FileListResponse | RouteErrorResponse:
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

    return await asyncio.to_thread(_list_files_sync, root, target, path)

@router.get("/files/read")
async def read_file(
    path: str = Query(...),
    workspace: str = Query(default=""),
    username: str = Query(...),
    offset: int = Query(default=0),
    limit: int = Query(default=200),
) -> FileReadTextResponse | FileReadBinaryResponse | RouteErrorResponse:
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

    ext = target.suffix.lower()
    is_binary = _is_binary_file(target, ext)
    is_image = ext in _IMAGE_EXTENSIONS
    mime_type = _IMAGE_MIME.get(ext, "")
    mtime = _format_time(target.stat().st_mtime)
    lang = _EXT_LANG.get(ext, "")

    if is_binary:
        return {
            "path": path,
            "is_binary": True,
            "is_image": is_image,
            "mime_type": mime_type,
            "size": size,
            "modified": mtime,
            "language": lang,
        }

    try:
        content, total_lines, has_more = await asyncio.to_thread(_read_text_window, target, offset, limit)
    except Exception as e:
        return {"error": f"读取失败: {e}"}

    return {
        "path": path,
        "language": lang,
        "content": content,
        "offset": offset,
        "limit": limit,
        "total_lines": total_lines,
        "has_more": has_more,
        "size": size,
        "modified": mtime,
        "is_binary": False,
        "is_image": False,
    }

@router.get("/files/raw")
async def raw_file(
    path: str = Query(...),
    workspace: str = Query(default=""),
    username: str = Query(...),
):
    """返回原始文件内容（用于图片预览、下载等）。"""
    root = _get_project_root(workspace, username)
    if not root:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "工作空间无关联项目路径"}, status_code=404)

    target = _safe_resolve(root, path)
    if not target or not target.exists() or not target.is_file():
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "文件不存在"}, status_code=404)

    ext = target.suffix.lower()
    media_type = _IMAGE_MIME.get(ext, "application/octet-stream")
    return FileResponse(target, media_type=media_type)

@router.get("/files/search")
async def search_files(
    query: str = Query(...),
    path: str = Query(default=""),
    workspace: str = Query(default=""),
    username: str = Query(...),
) -> FileSearchResponse | RouteErrorResponse:
    """递归搜索文件名。"""
    root = _get_project_root(workspace, username)
    if not root:
        return {"error": "工作空间无关联项目路径"}

    root = root.resolve()
    target = _safe_resolve(root, path) if path else root
    if not target or not target.exists():
        return {"error": "路径不存在"}

    return await asyncio.to_thread(_search_files_sync, root, target, query)

@router.get("/files/browse")
async def browse_dirs(path: str = Query(default=""), username: str = Query(...)) -> BrowseDirsResponse | RouteErrorResponse:
    if not path:
        path = str(Path.home())
    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        return {"error": f"路径不存在: {path}", "current": path, "parent": "", "dirs": []}
    return await asyncio.to_thread(_browse_dirs_sync, root)