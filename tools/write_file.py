"""文件写入工具"""
from pathlib import Path

from logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "将内容写入指定文件，会自动创建父目录",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
    },
}


def execute(args: dict) -> str:
    path = Path(args["path"])
    content = args["content"]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    logger.info(f"[写文件] {path} ({len(content)} 字符)")
    return f"已写入 {path}（{len(content)} 字符，{content.count(chr(10)) + 1} 行）"
