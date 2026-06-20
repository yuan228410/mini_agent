"""Shell 命令执行工具"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import subprocess

from ..logger import logger

definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "执行 shell 命令。默认超时 30 秒。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
                "cwd": {"type": "string", "description": "工作目录"},
            },
            "required": ["command"],
        },
    },
}


def execute_with_cwd(default_cwd: str | None, args: ToolArgs) -> str:
    command = args.get("command", "")
    if not command or not isinstance(command, str):
        return "Error: 缺少 command 参数"
    timeout = args.get("timeout", 30)

    cwd = args.get("cwd") or default_cwd or None
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30

    logger.info(f"[执行→] {command} (timeout={timeout}s, cwd={cwd})")
    try:
        # 强制使用 UTF-8 编码，避免乱码
        import os
        env = os.environ.copy()
        env["LANG"] = "en_US.UTF-8"
        env["LC_ALL"] = "en_US.UTF-8"
        
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, errors="replace",
            timeout=timeout, cwd=cwd, env=env, encoding="utf-8",
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout.rstrip("\n"))
        if result.stderr:
            parts.append("--- STDERR ---\n" + result.stderr.rstrip("\n"))
        output = "\n".join(parts) if parts else ""
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
    except subprocess.TimeoutExpired:
        output = f"Error: 命令超时（{timeout}s）"
    except OSError as e:
        output = f"Error: {e}"
    logger.debug(f"[执行←] len={len(output)}")
    return output


def execute(args: ToolArgs) -> str:
    return execute_with_cwd(None, args)
