"""Shell 命令执行工具"""
import subprocess

from logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": "在终端执行一条 shell 命令并返回输出",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"}
            },
            "required": ["command"]
        }
    }
}


def execute(args: dict) -> str:
    command = args["command"]
    logger.info(f"[执行→] {command}")
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    output = result.stdout or result.stderr
    logger.debug(f"[执行←] exit={result.returncode} len={len(output)}")
    return output