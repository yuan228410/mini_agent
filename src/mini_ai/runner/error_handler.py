"""错误处理策略

统一处理工具循环中的各类异常，提供：
- 异常分类
- 用户友好消息
- 恢复策略建议
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..exceptions import MiniAIError

from .state import LoopState
from ..utils import now_ts
from ..logger import logger

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
        state: LoopState
    ) -> dict[str, Any] | None:
        """处理异常
        
        Args:
            error: 异常对象
            state: 循环状态
        
        Returns:
            用户消息（dict），或 None 表示继续循环
        """
        from ..exceptions import (
            MiniAIError,
            ToolError,
            LLMError,
            ConfigError,
        )
        
        # MiniAI 统一异常
        if isinstance(error, MiniAIError):
            return self._handle_mini_ai_error(error, state)
        
        # 其他异常
        return self._handle_unknown_error(error, state)
    
    def _handle_mini_ai_error(
        self, 
        error: MiniAIError, 
        state: LoopState
    ) -> dict[str, Any] | None:
        """处理 MiniAI 异常"""
        from ..exceptions import ToolError, LLMError, ConfigError
        from datetime import datetime, timezone, timedelta
        
        _ts = now_ts()
        
        if isinstance(error, ConfigError):
            # 配置错误，不可恢复
            return {
                "role": "user",
                "content": f"⚠ 配置错误: {error.to_user_message()}",
                "timestamp": _ts,
            }
        
        if isinstance(error, LLMError):
            # LLM 错误，通常可重试
            if error.status_code == 429:
                # 速率限制
                return {
                    "role": "user",
                    "content": "⚠ 请求过于频繁，请稍后重试。",
                    "timestamp": _ts,
                }
            return {
                "role": "user",
                "content": f"⚠ LLM 调用失败: {error.to_user_message()}",
                "timestamp": _ts,
            }
        
        if isinstance(error, ToolError):
            # 工具错误（注意：正常情况下不会走这个分支，因为异常在 executor.execute_tools 内部被处理）
            # 但保留此分支以防万一
            state.record_error()
            
            if state.consecutive_errors >= self.max_consecutive_errors:
                # 连续错误过多，请求 LLM 总结
                return {
                    "role": "user",
                    "content": "⚠ 上述工具连续执行失败，请直接向用户说明情况并给出建议，不要再调用工具。",
                    "timestamp": _ts,
                }
            
            # 单次错误，通知 LLM
            return {
                "role": "user",
                "content": f"⚠ 工具执行失败: {error.to_user_message()}\n\n请检查参数是否正确，或尝试其他方案。",
                "timestamp": _ts,
            }
        
        # 其他 MiniAI 异常
        return {
            "role": "user",
            "content": f"⚠ 错误: {error.to_user_message()}",
            "timestamp": _ts,
        }
    
    def _handle_unknown_error(
        self,
        error: Exception,
        state: LoopState
    ) -> dict[str, Any] | None:
        """处理未知异常"""
        from datetime import datetime, timezone, timedelta

        _ts = now_ts()

        logger.error(f"[ErrorHandler] 未处理异常: {error}", exc_info=True)
        
        return {
            "role": "user",
            "content": f"⚠ 发生未知错误: {type(error).__name__}: {error}",
            "timestamp": _ts,
        }
    
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
