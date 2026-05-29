"""文件重命名/移动工具 — 跨设备安全移动"""
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "rename_file",
        "description": "重命名或移动文件/目录。支持跨设备移动（自动使用 shutil.move），目标已存在时返回错误（除非 force=true）。",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "原路径（文件或目录）"},
                "dst": {"type": "string", "description": "目标路径"},
                "force": {"type": "boolean", "description": "目标已存在时是否覆盖，默认 false"},
            },
            "required": ["src", "dst"],
        },
    },
}


def execute(args: dict) -> str:
    src_str = args.get("src", "")
    dst_str = args.get("dst", "")
    force = args.get("force", False)

    if not src_str or not isinstance(src_str, str):
        return "Error: 缺少 src 参数"
    if not dst_str or not isinstance(dst_str, str):
        return "Error: 缺少 dst 参数"

    src = Path(src_str).expanduser()
    dst = Path(dst_str).expanduser()

    if not src.exists():
        return f"Error: 源路径不存在: {src}"

    if dst.exists() and not force:
        return f"Error: 目标已存在: {dst}（如需覆盖请设置 force=true）"

    if dst.exists() and force:
        if dst.is_dir():
            import shutil
            shutil.rmtree(dst)
        else:
            dst.unlink()

    dst.parent.mkdir(parents=True, exist_ok=True)

    import shutil
    shutil.move(str(src), str(dst))
    logger.info(f"[重命名] {src} → {dst}")
    return f"已重命名: {src} → {dst}"
