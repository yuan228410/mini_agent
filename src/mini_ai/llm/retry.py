"""LLM 重试策略 — 智能重试、指数退避、错误分类"""
import time
import random
from typing import Callable, Any
from functools import wraps

from ..exceptions import LLMError
from ..logger import logger


class RetryStrategy:
    """重试策略配置
    
    Attributes:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        exponential_base: 指数退避基数
        jitter: 是否添加随机抖动
        retryable_status_codes: 可重试的 HTTP 状态码
        retryable_errors: 可重试的错误关键字
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_status_codes: set[int] | None = None,
        retryable_errors: set[str] | None = None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        
        # 可重试的 HTTP 状态码
        self.retryable_status_codes = retryable_status_codes or {
            408,  # Request Timeout
            429,  # Too Many Requests
            500,  # Internal Server Error
            502,  # Bad Gateway
            503,  # Service Unavailable
            504,  # Gateway Timeout
        }
        
        # 可重试的错误关键字
        self.retryable_errors = retryable_errors or {
            "timeout",
            "timed out",
            "connection",
            "network",
            "rate limit",
            "overloaded",
            "capacity",
            "temporarily unavailable",
        }
    
    def should_retry(self, error: Exception, attempt: int) -> bool:
        """判断是否应该重试
        
        Args:
            error: 发生的异常
            attempt: 当前尝试次数（从 0 开始）
        
        Returns:
            是否应该重试
        """
        if attempt >= self.max_retries:
            return False
        
        # LLMError 特殊处理
        if isinstance(error, LLMError):
            # 检查状态码
            if error.status_code in self.retryable_status_codes:
                return True
            
            # 检查错误消息
            error_msg = str(error).lower()
            for keyword in self.retryable_errors:
                if keyword in error_msg:
                    return True
            
            # 如果有 retry_after，说明服务端建议重试
            if error.retry_after is not None:
                return True
            
            return False
        
        # 其他异常（网络错误等）
        error_msg = str(error).lower()
        for keyword in self.retryable_errors:
            if keyword in error_msg:
                return True
        
        return False
    
    def get_delay(self, attempt: int, error: Exception | None = None) -> float:
        """计算重试延迟
        
        Args:
            attempt: 当前尝试次数（从 0 开始）
            error: 发生的异常（用于提取 retry_after）
        
        Returns:
            延迟秒数
        """
        # 如果服务端指定了 retry_after，优先使用
        if isinstance(error, LLMError) and error.retry_after is not None:
            delay = error.retry_after
        else:
            # 指数退避
            delay = self.base_delay * (self.exponential_base ** attempt)
        
        # 限制最大延迟
        delay = min(delay, self.max_delay)
        
        # 添加随机抖动（避免重试风暴）
        if self.jitter:
            delay = delay * (0.5 + random.random())
        
        return delay


def with_retry(
    strategy: RetryStrategy | None = None,
    on_retry: Callable[[Exception, int, float], None] | None = None,
):
    """重试装饰器
    
    Args:
        strategy: 重试策略（None 则使用默认）
        on_retry: 重试回调函数 (error, attempt, delay)
    
    Returns:
        装饰器函数
    
    Example:
        @with_retry(RetryStrategy(max_retries=3))
        def call_llm():
            ...
    """
    if strategy is None:
        strategy = RetryStrategy()
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_error = None
            
            for attempt in range(strategy.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    
                    if not strategy.should_retry(e, attempt):
                        raise
                    
                    delay = strategy.get_delay(attempt, e)
                    
                    if on_retry:
                        on_retry(e, attempt, delay)
                    else:
                        logger.warning(
                            f"[重试] {func.__name__} 第 {attempt + 1}/{strategy.max_retries} 次: {e}，"
                            f"{delay:.1f}s 后重试"
                        )
                    
                    time.sleep(delay)
            
            # 所有重试都失败
            raise last_error
        
        return wrapper
    return decorator


# 预定义策略
DEFAULT_STRATEGY = RetryStrategy()

# 保守策略（更多重试，更长延迟）
CONSERVATIVE_STRATEGY = RetryStrategy(
    max_retries=5,
    base_delay=2.0,
    max_delay=120.0,
)

# 激进策略（快速失败）
AGGRESSIVE_STRATEGY = RetryStrategy(
    max_retries=2,
    base_delay=0.5,
    max_delay=10.0,
)
