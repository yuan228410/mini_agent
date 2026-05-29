"""文件写入工具"""
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "将内容写入指定文件，会自动创建父目录。支持覆盖或追加模式。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "写入模式：overwrite 覆盖（默认）, append 追加到末尾",
                },
            },
            "required": ["path", "content"],
        },
    },
}

_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 10MB 安全阀


def execute(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str or not isinstance(path_str, str):
        return "Error: write_file 缺少 path 参数（字符串类型），请提供完整的文件路径后重试，不要重复空调用"
    content = args.get("content", "")
    if isinstance(content, str):
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > _MAX_WRITE_BYTES:
            return f"Error: 内容过大（{content_bytes} bytes），超过限值 {_MAX_WRITE_BYTES} bytes，请分批写入"
    path = Path(path_str)
    mode = args.get("mode", "overwrite")

    path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "append":
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[追加] {path} (+{len(content)} 字符)")
        return f"已追加到 {path}（+{len(content)} 字符）"
    else:
        path.write_text(content, encoding="utf-8")
        logger.info(f"[写文件] {path} ({len(content)} 字符)")
        return f"已写入 {path}（{len(content)} 字符，{content.count(chr(10)) + 1} 行）"