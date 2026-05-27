"""文件内容搜索工具 — grep + glob"""
import os
import re
import subprocess

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": (
            "在指定目录中搜索文件内容（类似 grep）。"
            "支持正则表达式、文件类型过滤、递归搜索。返回匹配行及其文件路径和行号。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式（支持正则表达式）"},
                "path": {"type": "string", "description": "搜索目录，默认当前目录"},
                "include": {"type": "string", "description": "文件名 glob 过滤，如 '*.py'、'*.ts'"},
                "max_results": {"type": "integer", "description": "最大返回条数，默认 50"},
            },
            "required": ["pattern"],
        },
    },
}


def execute(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    include = args.get("include", "")
    max_results = args.get("max_results", 50)
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 50

    cmd = ["grep", "-rn", "--color=never"]
    if include:
        cmd.extend(["--include", include])
    cmd.extend([pattern, path])

    logger.info(f"[搜索→] pattern='{pattern}' path={path} include={include}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        lines = result.stdout.splitlines()
    except subprocess.TimeoutExpired:
        return "Error: 搜索超时（15s）"
    except OSError as e:
        return f"Error: {e}"

    if not lines:
        return f"未找到匹配: '{pattern}'"

    total = len(lines)
    output = lines[:max_results]
    result_text = "\n".join(output)
    if total > max_results:
        result_text += f"\n\n... 共 {total} 条匹配，已显示前 {max_results} 条"
    else:
        result_text += f"\n\n共 {total} 条匹配"

    return result_text
