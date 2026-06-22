"""File browsing and preview use cases for Web adapters."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .file_operations import browse_dirs_at, list_files_at, read_text_window, search_files_at
from .file_paths import project_root, safe_resolve
from .file_types import EXT_LANG, IMAGE_EXTENSIONS, IMAGE_MIME, format_time, is_binary_file


def list_files(workspace_manager, *, workspace: str, path: str, fallback_cwd: Path | None = None) -> dict[str, Any]:
    """List files under a workspace path."""

    root = project_root(workspace_manager, workspace, fallback_cwd)
    if not root:
        return {"error": "工作空间无关联项目路径"}
    root = root.resolve()
    target = safe_resolve(root, path) if path else root
    if not target:
        return {"error": "路径不合法"}
    if not target.exists():
        return {"error": f"路径不存在: {path}"}
    if not target.is_dir():
        return {"error": "不是目录"}
    return list_files_at(root, target, path)


def read_file(workspace_manager, *, workspace: str, path: str, offset: int = 0, limit: int = 200, fallback_cwd: Path | None = None) -> dict[str, Any]:
    """Read a text window or return binary metadata for a workspace file."""

    root = project_root(workspace_manager, workspace, fallback_cwd)
    if not root:
        return {"error": "工作空间无关联项目路径"}

    target = safe_resolve(root, path)
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
    is_binary = is_binary_file(target, ext)
    is_image = ext in IMAGE_EXTENSIONS
    mime_type = IMAGE_MIME.get(ext, "")
    mtime = format_time(target.stat().st_mtime)
    lang = EXT_LANG.get(ext, "")

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
        content, total_lines, has_more = read_text_window(target, offset, limit)
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


def raw_file(workspace_manager, *, workspace: str, path: str, fallback_cwd: Path | None = None) -> dict[str, Any]:
    """Return raw file path and media type for download/preview."""

    root = project_root(workspace_manager, workspace, fallback_cwd)
    if not root:
        return {"error": "工作空间无关联项目路径", "status_code": 404}
    target = safe_resolve(root, path)
    if not target or not target.exists() or not target.is_file():
        return {"error": "文件不存在", "status_code": 404}
    return {"path": target, "media_type": IMAGE_MIME.get(target.suffix.lower(), "application/octet-stream")}


def search_files(workspace_manager, *, workspace: str, path: str, query: str, fallback_cwd: Path | None = None) -> dict[str, Any]:
    """Search files by name under a workspace path."""

    root = project_root(workspace_manager, workspace, fallback_cwd)
    if not root:
        return {"error": "工作空间无关联项目路径"}
    root = root.resolve()
    target = safe_resolve(root, path) if path else root
    if not target or not target.exists():
        return {"error": "路径不存在"}
    return search_files_at(root, target, query)


def browse_dirs(path: str = "") -> dict[str, Any]:
    """Browse local directories."""

    if not path:
        path = str(Path.home())
    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        return {"error": f"路径不存在: {path}", "current": path, "parent": "", "dirs": []}
    return browse_dirs_at(root)
