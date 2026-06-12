"""工具模块基类 — 统一 definition 生成 + execute 抽象 + 截断辅助

使用方式：
    class MyTool(ToolBase):
        name = "my_tool"
        description = "做某事"
        parameters = {"type": "object", "properties": {...}, "required": [...]}

        @staticmethod
        def execute(args: dict) -> str:
            ...

    # 向后兼容（供 _registry.add_tools 使用）
    definition = MyTool.definition()
    execute = MyTool.execute
"""
from __future__ import annotations

from ..config import TOOL

_MAX_RESULT_CHARS = TOOL.get("max_result_chars", 8000)


class ToolBase:
    """工具模块基类

    子类只需定义 name / description / parameters / execute()。
    definition() 自动生成 OpenAI function calling 格式。
    """

    name: str = ""
    description: str = ""
    parameters: dict = {}

    @classmethod
    def definition(cls) -> dict:
        """生成 OpenAI function calling 格式的工具定义"""
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.parameters,
            },
        }

    @staticmethod
    def execute(args: dict) -> str:
        """执行工具逻辑，子类必须实现"""
        raise NotImplementedError

    @staticmethod
    def _truncate(output: str, max_chars: int = _MAX_RESULT_CHARS) -> str:
        """截断过长输出"""
        if len(output) <= max_chars:
            return output
        return output[:max_chars] + f"\n[已截断，原长 {len(output)} 字符]"
