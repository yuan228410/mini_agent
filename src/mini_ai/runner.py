"""Agent 执行器 — 统一的 LLM 工具循环

此文件保持向后兼容，实际实现已重构到 runner/ 子模块。

模块结构：
- runner/state.py: 循环状态管理
- runner/executor.py: LLM 调用和工具执行
- runner/error_handler.py: 错误处理策略
- runner/loop.py: 主循环逻辑
"""

# 从子模块导入，保持向后兼容
from .runner.loop import run_tool_loop, run_agent
from .runner.state import LoopState
from .runner.executor import ToolExecutor
from .runner.error_handler import ErrorHandler

__all__ = [
    "run_tool_loop",
    "run_agent",
    "LoopState",
    "ToolExecutor",
    "ErrorHandler",
]
