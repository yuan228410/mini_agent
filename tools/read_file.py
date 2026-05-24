"""文件读取工具"""
from pathlib import Path

from logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取指定文件的内容，支持行号范围筛选",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行号（从1开始），不传则从第1行开始"},
                "end_line": {"type": "integer", "description": "结束行号（含），不传则读到文件末尾"},
            },
            "required": ["path"],
        },
    },
}


def execute(args: dict) -> str:
    path = Path(args["path"])
    if not path.exists():
        return f"Error: 文件不存在: {path}"
    if not path.is_file():
        return f"Error: 不是文件: {path}"
    if path.stat().st_size > 500_000:
        return f"Error: 文件过大 ({path.stat().st_size} bytes)，请用 start_line/end_line 分段读取"

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return f"Error: 文件编码不是 UTF-8: {path}"

    start = max(1, args.get("start_line", 1)) - 1
    end = args.get("end_line", len(lines))
    selected = lines[start:end]

    logger.info(f"[读文件] {path} L{start+1}-{start+len(selected)}")
    result = []
    for i, line in enumerate(selected, start=start + 1):
        result.append(f"{i:>6}|{line}")
    return "\n".join(result)
