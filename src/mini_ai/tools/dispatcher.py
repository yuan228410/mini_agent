"""Single tool-call dispatch helpers.

The registry owns tool registration and session resources.  This module keeps the
repeatable per-call mechanics (argument parsing and error text construction) out
of that registry so scheduler/dispatcher concerns can evolve independently.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass

from ..core.runtime_types import ToolArgs
from ..core.tool_models import ToolCall


@dataclass(frozen=True, slots=True)
class ToolArgumentError(Exception):
    tool_name: str
    raw_arguments: str
    cause: Exception

    def user_message(self) -> str:
        return (
            "⚠ 工具调用失败：参数 JSON 解析错误\n\n"
            f"工具: {self.tool_name}\n"
            f"原始参数: {self.raw_arguments[:200]}\n"
            f"错误: {type(self.cause).__name__}: {self.cause}\n\n"
            "请检查参数格式是否正确。"
        )


def parse_tool_args(tc: ToolCall) -> ToolArgs:
    raw_args = tc.function.arguments or ""
    try:
        return json.loads(raw_args) if raw_args else {}
    except (json.JSONDecodeError, TypeError) as exc:
        raise ToolArgumentError(tc.function.name, raw_args, exc) from exc


def format_tool_exception(name: str, args_summary: str, exc: Exception) -> str:
    from ..exceptions import MiniAIError

    if isinstance(exc, MiniAIError):
        output = (
            f"⚠ {exc.to_user_message()}\n\n"
            f"工具: {name}\n"
            f"参数: {args_summary}\n"
            f"错误类型: {type(exc).__name__}\n"
            f"可恢复: {'是' if exc.recoverable else '否'}"
        )
        if hasattr(exc, "context") and exc.context:
            output += f"\n上下文: {exc.context}"
        return output

    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError)):
        return (
            "⚠ 文件操作失败\n\n"
            f"工具: {name}\n"
            f"错误: {type(exc).__name__}: {exc}\n"
            f"参数: {args_summary}\n\n"
            "请检查路径是否正确，权限是否足够。"
        )

    return (
        "⚠ 工具执行异常\n\n"
        f"工具: {name}\n"
        f"错误: {type(exc).__name__}: {exc}\n"
        f"参数: {args_summary}\n\n"
        f"堆栈:\n{traceback.format_exc()}"
    )
