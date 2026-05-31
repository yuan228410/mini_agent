"""统一异常体系

提供分层异常类，支持：
- 可恢复性标记
- 错误上下文
- 用户友好消息
"""
from __future__ import annotations

__all__ = [
    'MiniAIError',
    'ConfigError',
    'LLMError',
    'ToolError',
    'ResourceNotFoundError',
    'PermissionDeniedError',
    'ValidationError',
    'TeamError',
    'WorkflowError',
]

from typing import Any


class MiniAIError(Exception):
    """所有 mini-ai 异常的基类
    
    Attributes:
        message: 错误消息
        recoverable: 是否可恢复（不影响后续对话）
        context: 额外上下文信息
    """
    
    def __init__(
        self, 
        message: str, 
        *, 
        recoverable: bool = True,
        user_message: str | None = None,
        **context: Any
    ):
        super().__init__(message)
        self.recoverable = recoverable
        self.user_message = user_message or message
        self.context = context
    
    def to_user_message(self) -> str:
        """返回用户可见的友好消息"""
        return self.user_message
    
    def __str__(self) -> str:
        parts = [self.args[0]]
        if self.context:
            parts.append(f"context={self.context}")
        if not self.recoverable:
            parts.append("[不可恢复]")
        return " | ".join(parts)


class ConfigError(MiniAIError):
    """配置加载/校验失败"""
    
    def __init__(
        self, 
        message: str, 
        *, 
        config_path: str = "",
        field: str = "",
        **context: Any
    ):
        super().__init__(
            message, 
            recoverable=False,
            config_path=config_path,
            field=field,
            **context
        )
        self.config_path = config_path
        self.field = field


class LLMError(MiniAIError):
    """LLM 调用异常"""
    
    def __init__(
        self, 
        message: str, 
        *, 
        provider: str = "",
        model: str = "",
        status_code: int = 0,
        retry_after: float | None = None,
        **context: Any
    ):
        super().__init__(
            message,
            recoverable=True,  # LLM 错误通常可重试
            provider=provider,
            model=model,
            status_code=status_code,
            **context
        )
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retry_after = retry_after
    
    def to_user_message(self) -> str:
        if self.status_code == 429:
            return "⚠ 请求过于频繁，请稍后重试"
        if self.status_code >= 500:
            return f"⚠ LLM 服务暂时不可用 ({self.provider})"
        return f"⚠ LLM 调用失败: {self.user_message}"


class ToolError(MiniAIError):
    """工具执行异常"""
    
    def __init__(
        self, 
        tool_name: str, 
        message: str, 
        *, 
        recoverable: bool = True,
        **context: Any
    ):
        super().__init__(
            f"[{tool_name}] {message}",
            recoverable=recoverable,
            tool_name=tool_name,
            **context
        )
        self.tool_name = tool_name


class ResourceNotFoundError(ToolError):
    """资源不存在（文件、目录、URL 等）"""
    
    def __init__(
        self, 
        tool_name: str, 
        resource: str,
        *,
        resource_type: str = "资源",
        **context: Any
    ):
        super().__init__(
            tool_name, 
            f"{resource_type}不存在: {resource}",
            recoverable=True,
            resource=resource,
            resource_type=resource_type,
            **context
        )
        self.resource = resource
        self.resource_type = resource_type
    
    def to_user_message(self) -> str:
        return f"⚠ {self.resource_type}不存在: {self.resource}"


class PermissionDeniedError(ToolError):
    """权限不足"""
    
    def __init__(
        self, 
        tool_name: str, 
        resource: str,
        *,
        action: str = "访问",
        **context: Any
    ):
        super().__init__(
            tool_name,
            f"无权限{action}: {resource}",
            recoverable=False,
            resource=resource,
            action=action,
            **context
        )
        self.resource = resource
        self.action = action


class ValidationError(ToolError):
    """参数校验失败"""
    
    def __init__(
        self,
        tool_name: str,
        param_name: str,
        message: str,
        *,
        expected: str = "",
        actual: Any = None,
        **context: Any
    ):
        super().__init__(
            tool_name,
            f"参数 '{param_name}' {message}",
            recoverable=True,
            param_name=param_name,
            expected=expected,
            actual=str(actual) if actual is not None else None,
            **context
        )
        self.param_name = param_name
        self.expected = expected


class TeamError(MiniAIError):
    """Team 协作异常"""
    
    def __init__(
        self,
        message: str,
        *,
        teammate_name: str = "",
        **context: Any
    ):
        super().__init__(
            message,
            recoverable=True,
            teammate_name=teammate_name,
            **context
        )
        self.teammate_name = teammate_name


class WorkflowError(MiniAIError):
    """工作流异常"""
    
    def __init__(
        self,
        message: str,
        *,
        task_id: str = "",
        **context: Any
    ):
        super().__init__(
            message,
            recoverable=True,
            task_id=task_id,
            **context
        )
        self.task_id = task_id
