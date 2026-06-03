"""文件读取工具 — 优化版：大文件逐行读取，避免内存浪费"""
from pathlib import Path

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取文件内容，支持行号范围筛选。大文件（>500KB）必须分段读取。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行号（从1开始）"},
                "end_line": {"type": "integer", "description": "结束行号（含）"},
            },
            "required": ["path"],
        },
    },
}

_MAX_FILE_SIZE = 500_000  # 500KB


def execute(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str or not isinstance(path_str, str):
        return "Error: read_file 缺少 path 参数（字符串类型），请提供正确的文件路径后重试"
    
    path = Path(path_str)
    if not path.exists():
        return f"Error: 文件不存在: {path}"
    if not path.is_file():
        return f"Error: 不是文件: {path}"
    
    file_size = path.stat().st_size
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    
    # 如果指定了行号范围，使用逐行读取（避免读取整个大文件）
    if start_line is not None or end_line is not None:
        return _read_by_lines(path, start_line, end_line)
    
    # 未指定行号范围，检查文件大小
    if file_size > _MAX_FILE_SIZE:
        return f"Error: 文件过大 ({file_size} bytes)，请用 start_line/end_line 分段读取"
    
    # 小文件直接读取
    return _read_full(path)


def _read_by_lines(path: Path, start_line: int | None, end_line: int | None) -> str:
    """逐行读取文件，避免内存浪费"""
    start = max(1, start_line or 1)
    end = end_line or float('inf')
    
    lines = []
    try:
        with path.open('r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                if i > end:
                    break
                if i >= start:
                    lines.append((i, line.rstrip('\n')))
    except UnicodeDecodeError:
        return f"Error: 文件编码不是 UTF-8: {path}"
    except Exception as e:
        return f"Error: 读取文件失败: {e}"
    
    if not lines:
        return f"Error: 指定行号范围无内容 (L{start}-{end})"
    
    logger.info(f"[读文件] {path} L{lines[0][0]}-{lines[-1][0]}")
    
    # 格式化输出
    result = []
    for line_num, content in lines:
        result.append(f"{line_num:>6}|{content}")
    return "\n".join(result)


def _read_full(path: Path) -> str:
    """读取整个文件（仅用于小文件）"""
    try:
        content = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return f"Error: 文件编码不是 UTF-8: {path}"
    
    lines = content.splitlines()
    logger.info(f"[读文件] {path} ({len(lines)} 行)")
    
    # 格式化输出
    result = []
    for i, line in enumerate(lines, 1):
        result.append(f"{i:>6}|{line}")
    return "\n".join(result)
