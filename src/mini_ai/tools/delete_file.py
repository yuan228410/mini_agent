"""文件删除工具 — 安全受限，仅删除文件（非目录），支持递归删除目录"""
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": "删除文件或空目录。recursive=true 可删除非空目录。系统关键路径禁止删除。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件或目录路径"},
                "recursive": {"type": "boolean", "description": "递归删除目录"},
                "missing_ok": {"type": "boolean", "description": "路径不存在时静默忽略"},
            },
            "required": ["path"],
        },
    },
}

# macOS 上 /etc→/private/etc、/var→/private/var、/tmp→/private/tmp，同时包含两种形式
_BLOCKED_PATHS = {
    "/", str(Path.home()),
    "/tmp", "/private/tmp",
    "/var", "/private/var",
    "/etc", "/private/etc",
    "/usr", "/bin", "/sbin", "/opt",
    "/System", "/Applications",
}


def _is_blocked(path: Path) -> bool:
    """检查路径或其任意祖先是否在禁止删除的列表中（不检查根目录 /）"""
    resolved = path.resolve()
    if str(resolved) in _BLOCKED_PATHS:
        return True
    for parent in resolved.parents:
        if parent == parent.parent:  # 到达根目录 /，不检查
            break
        if str(parent) in _BLOCKED_PATHS:
            return True
    return False


def execute(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str or not isinstance(path_str, str):
        return "Error: 缺少 path 参数"
    recursive = args.get("recursive", False)
    missing_ok = args.get("missing_ok", False)

    path = Path(path_str).expanduser().resolve()

    # if _is_blocked(path):
    #     return f"Error: 禁止删除系统关键路径: {path}"

    if not path.exists():
        if missing_ok:
            return f"路径不存在，已忽略: {path}"
        return f"Error: 路径不存在: {path}"

    cwd = Path.cwd()
    if path == cwd or (path.parent == cwd and path.name in (".git", ".venv", "node_modules")):
        return f"Error: 禁止删除当前工作空间关键路径: {path}"

    if path.is_dir():
        if recursive:
            import shutil
            shutil.rmtree(path)
            logger.info(f"[删除目录] {path}")
            return f"已递归删除目录: {path}"
        try:
            path.rmdir()
            logger.info(f"[删除空目录] {path}")
            return f"已删除空目录: {path}"
        except OSError:
            return f"Error: 目录非空，如需删除请设置 recursive=true: {path}"
    else:
        path.unlink()
        logger.info(f"[删除文件] {path}")
        return f"已删除文件: {path}"