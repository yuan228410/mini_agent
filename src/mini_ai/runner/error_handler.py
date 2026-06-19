"""错误处理策略

统一处理工具循环中的各类异常，提供：
- 异常分类
- 用户友好消息
- 恢复策略建议
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ..core.runtime_types import MessageDict
from ..exceptions import MiniAIError
from .state import LoopState
from ..utils import now_ts
from ..logger import logger


class ErrorCategory(StrEnum):
    CONFIG = "config"
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM = "llm"
    TOOL = "tool"
    TOOL_CONSECUTIVE = "tool_consecutive"
    MINI_AI = "mini_ai"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ErrorMessage:
    category: ErrorCategory
    content: str
    timestamp: str
    role: str = "user"

    def to_message(self) -> MessageDict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "error_category": self.category.value,
        }


def _error_message(category: ErrorCategory, content: str) -> MessageDict:
    return ErrorMessage(category=category, content=content, timestamp=now_ts()).to_message()


class ErrorHandler:
    """统一错误处理器
    
    根据异常类型决定：
    - 是否可恢复
    - 返回什么用户消息
    - 是否终止循环
    """
    
    def __init__(self, max_consecutive_errors: int = 3):
        """
        Args:
            max_consecutive_errors: 最大连续错误数
        """
        self.max_consecutive_errors = max_consecutive_errors
    
    def handle(
        self,
        error: Exception,
        state: LoopState,
    ) -> MessageDict | None:
        """处理异常
        
        Args:
            error: 异常对象
            state: 循环状态
        
        Returns:
            用户消息（dict），或 None 表示继续循环
        """
        from ..exceptions import MiniAIError
        
        # MiniAI 统一异常
        if isinstance(error, MiniAIError):
            return self._handle_mini_ai_error(error, state)
        
        # 其他异常
        return self._handle_unknown_error(error, state)
    
    def _handle_mini_ai_error(
        self,
        error: MiniAIError,
        state: LoopState,
    ) -> MessageDict | None:
        """处理 MiniAI 异常"""
        from ..exceptions import ToolError, LLMError, ConfigError

        if isinstance(error, ConfigError):
            # 配置错误，不可恢复
            return _error_message(ErrorCategory.CONFIG, f"⚠ 配置错误: {error.to_user_message()}")

        if isinstance(error, LLMError):
            # LLM 错误，通常可重试
            if error.status_code == 429:
                # 速率限制
                return _error_message(ErrorCategory.LLM_RATE_LIMIT, "⚠ 请求过于频繁，请稍后重试。")
            return _error_message(ErrorCategory.LLM, f"⚠ LLM 调用失败: {error.to_user_message()}")

        # TODO: 工具模块尚未接入异常体系，ToolError 从未被 raise
        # 当工具模块统一抛 ToolError 后，此分支才会生效
        if isinstance(error, ToolError):
            state.record_error()

            if state.consecutive_errors >= self.max_consecutive_errors:
                # 连续错误过多，请求 LLM 总结
                return _error_message(
                    ErrorCategory.TOOL_CONSECUTIVE,
                    "⚠ 上述工具连续执行失败，请直接向用户说明情况并给出建议，不要再调用工具。",
                )

            # 单次错误，通知 LLM
            return _error_message(
                ErrorCategory.TOOL,
                f"⚠ 工具执行失败: {error.to_user_message()}\n\n请检查参数是否正确，或尝试其他方案。",
            )

        # 其他 MiniAI 异常
        return _error_message(ErrorCategory.MINI_AI, f"⚠ 错误: {error.to_user_message()}")
    
    def _handle_unknown_error(
        self,
        error: Exception,
        state: LoopState,
    ) -> MessageDict | None:
        """处理未知异常"""
        logger.error(f"[ErrorHandler] 未处理异常: {error}", exc_info=True)
        return _error_message(ErrorCategory.UNKNOWN, f"⚠ 发生未知错误: {type(error).__name__}: {error}")
    
    def should_terminate(self, error: Exception) -> bool:
        """判断异常是否应终止循环
        
        Args:
            error: 异常对象
        
        Returns:
            True 如果应终止
        """
        from ..exceptions import ConfigError
        
        # 配置错误终止循环
        if isinstance(error, ConfigError):
            return True
        
        return False
