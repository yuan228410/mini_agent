"""目录列表工具 — 优化版：递归提前终止，避免遍历过多"""
import os
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "列出目录内容，支持递归。最多返回 500 行。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认当前目录"},
                "recursive": {"type": "boolean", "description": "是否递归列出子目录"},
                "max_depth": {"type": "integer", "description": "递归最大深度，默认 3"},
                "include": {"type": "string", "description": "文件名 glob 过滤，如 '*.py'"},
            },
        },
    },
}

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build", ".egg-info"}
_MAX_LINES = 500


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
    
    # 截断提示
    if len(lines) > _MAX_LINES:
        return "\n".join(lines[:_MAX_LINES]) + f"\n... 已截断，共 {len(lines)} 项"
    return "\n".join(lines)


def _walk(path: Path, lines: list, recursive: bool, max_depth: int, depth: int, include: str) -> bool:
    """递归遍历目录
    
    Returns:
        bool: 是否应该停止遍历（已达到最大行数）
    """
    # 提前检查：深度和行数
    if depth > max_depth:
        return False
    if len(lines) >= _MAX_LINES:
        return True  # 停止遍历

    indent = "  " * depth
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        lines.append(f"{indent}[权限不足]")
        return False

    for entry in entries:
        # 再次检查行数（每次添加前）
        if len(lines) >= _MAX_LINES:
            return True
        
        # 跳过忽略目录
        if entry.name in _IGNORE_DIRS:
            continue
        if entry.name.startswith(".") and entry.is_dir() and entry.name in _IGNORE_DIRS:
            continue

        if entry.is_dir():
            lines.append(f"{indent}{entry.name}/")
            if recursive:
                # 递归调用，检查是否需要停止
                if _walk(entry, lines, recursive, max_depth, depth + 1, include):
                    return True
        else:
            if include:
                if not entry.match(include):
                    continue
            lines.append(f"{indent}{entry.name}")
    
    return False
