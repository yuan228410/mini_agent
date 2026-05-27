"""Shell 命令执行工具"""
import subprocess

from ..logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "在终端执行一条 shell 命令并返回输出。支持指定超时和工作目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
            },
            "required": ["command"],
        },
    },
}


def execute(args: dict) -> str:
    command = args.get("command", "")
    if not command or not isinstance(command, str):
        return "Error: 缺少 command 参数"
    timeout = args.get("timeout", 30)
    cwd = args.get("cwd") or None
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30

    logger.info(f"[执行→] {command} (timeout={timeout}s, cwd={cwd})")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        output = result.stdout or result.stderr
    except subprocess.TimeoutExpired:
        output = f"Error: 命令超时（{timeout}s）"
    except OSError as e:
        output = f"Error: {e}"
    logger.debug(f"[执行←] len={len(output)}")
    return output
