"""目录列表工具"""
import os
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "列出指定目录的文件和子目录。支持递归展示文件树。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认当前目录"},
                "recursive": {"type": "boolean", "description": "是否递归列出子目录，默认 false"},
                "max_depth": {"type": "integer", "description": "递归最大深度，默认 3"},
                "include": {"type": "string", "description": "文件名 glob 过滤，如 '*.py'"},
            },
        },
    },
}

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build", ".egg-info"}


def execute(args: dict) -> str:
    path = Path(args.get("path", "."))
    recursive = args.get("recursive", False)
    max_depth = args.get("max_depth", 3)
    include = args.get("include", "")
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        max_depth = 3

    if not path.exists():
        return f"Error: 目录不存在: {path}"
    if not path.is_dir():
        return f"Error: 不是目录: {path}"

    lines = []
    _walk(path, lines, recursive, max_depth, 0, include)

    if not lines:
        return f"目录为空: {path}"

    logger.info(f"[列目录] {path} ({len(lines)} 项)")
    return "\n".join(lines[:500])


def _walk(path: Path, lines: list, recursive: bool, max_depth: int, depth: int, include: str):
    if depth > max_depth:
        return
    if len(lines) > 500:
        return

    indent = "  " * depth
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        lines.append(f"{indent}[权限不足]")
        return

    for entry in entries:
        if entry.name.startswith(".") and entry.is_dir() and entry.name in _IGNORE_DIRS:
            continue
        if entry.name in _IGNORE_DIRS:
            continue

        if entry.is_dir():
            lines.append(f"{indent}{entry.name}/")
            if recursive:
                _walk(entry, lines, recursive, max_depth, depth + 1, include)
        else:
            if include:
                if not entry.match(include):
                    continue
            lines.append(f"{indent}{entry.name}")
