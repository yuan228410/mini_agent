"""文件部分编辑工具 — search-and-replace 模式"""
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "对文件进行部分编辑（搜索替换），无需重写整个文件。"
            "找到 old_string 并替换为 new_string。如果 old_string 出现多次，只替换第一个。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "要查找的原始字符串（精确匹配）"},
                "new_string": {"type": "string", "description": "替换为的新字符串"},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配，默认 false（只替换第一个）"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


def execute(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str:
        return "Error: 缺少 path 参数"
    path = Path(path_str)
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    if not old_string:
        return "Error: 缺少 old_string 参数"
    replace_all = args.get("replace_all", False)

    if not path.exists():
        return f"Error: 文件不存在: {path}"

    content = path.read_text(encoding="utf-8")

    if old_string not in content:
        return f"Error: 未找到匹配内容。文件中不包含指定的 old_string"

    if replace_all:
        count = content.count(old_string)
        new_content = content.replace(old_string, new_string)
    else:
        count = 1
        new_content = content.replace(old_string, new_string, 1)

    path.write_text(new_content, encoding="utf-8")
    logger.info(f"[编辑] {path} 替换 {count} 处")
    return f"已编辑 {path}（替换 {count} 处）"
