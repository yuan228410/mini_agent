"""文件内容搜索工具 — grep（优先） + Python fallback"""
import os
import re
import shutil
import subprocess
import fnmatch

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
                "path": {"type": "string", "description": "搜索目录（必填）"},
                "include": {"type": "string", "description": "文件名 glob 过滤，如 '*.py'、'*.ts'"},
                "max_results": {"type": "integer", "description": "最大返回条数，默认 50"},
            },
            "required": ["pattern"],
        },
    },
}


def _py_search(pattern: str, root: str, include: str, max_results: int):
    """纯 Python 实现的 grep fallback（不依赖外部 grep 命令）。
    返回 (results, error) 二元组，error 非空时 results 无效。"""
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return [], f"正则语法错误: {e}"
    _SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build", ".egg-info"}
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if include:
                if not fnmatch.fnmatch(fn, include):
                    continue
            fpath = os.path.join(dirpath, fn)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if compiled.search(line):
                            results.append((fpath, lineno, line.rstrip("\n")))
                            if len(results) >= max_results:
                                return results, ""
            except (OSError, UnicodeDecodeError):
                continue
    return results, ""


def execute(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = args.get("path", "")
    include = args.get("include", "")
    max_results = args.get("max_results", 50)
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 50

    if not pattern:
        return "Error: 缺少 pattern 参数"

    if not path:
        return "Error: 请指定搜索目录（path 参数）"

    logger.info(f"[搜索→] pattern='{pattern}' path={path} include={include}")

    has_grep = shutil.which("grep") is not None

    if has_grep:
        cmd = ["grep", "-rn", "--color=never"]
        if include:
            cmd.extend(["--include", include])
        cmd.extend([pattern, path])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 2:
                err = result.stderr.strip()
                return f"Error: 搜索失败: {err}" if err else f"Error: grep 返回错误 (exit code 2)"
            lines = result.stdout.splitlines()
        except subprocess.TimeoutExpired:
            return "Error: 搜索超时（15s）"
        except OSError as e:
            return f"Error: {e}"
    else:
        logger.info(f"[搜索] grep 不可用，使用 Python fallback")
        results, err = _py_search(pattern, path, include, max_results)
        if err:
            return f"Error: {err}"
        lines = [f"{fp}:{ln}:{line}" for fp, ln, line in results]

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
