"""测试 LLM 重试策略"""
import pytest
from src.mini_ai.llm.retry import RetryStrategy
from src.mini_ai.exceptions import LLMError


def test_should_retry_with_retryable_status_codes():
    """测试可重试的状态码"""
    strategy = RetryStrategy()
    
    # 429 应该重试
    error = LLMError("Rate limit", status_code=429)
    assert strategy.should_retry(error, 0) is True
    
    # 500 应该重试
    error = LLMError("Server error", status_code=500)
    assert strategy.should_retry(error, 0) is True
    
    # 503 应该重试
    error = LLMError("Service unavailable", status_code=503)
    assert strategy.should_retry(error, 0) is True


def test_should_not_retry_with_non_retryable_status_codes():
    """测试不可重试的状态码"""
    strategy = RetryStrategy()
    
    # 400 不应该重试
    error = LLMError("Bad request", status_code=400)
    assert strategy.should_retry(error, 0) is False
    
    # 401 不应该重试
    error = LLMError("Unauthorized", status_code=401)
    assert strategy.should_retry(error, 0) is False
    
    # 404 不应该重试
    error = LLMError("Not found", status_code=404)
    assert strategy.should_retry(error, 0) is False


def test_should_retry_with_retryable_keywords():
    """测试可重试的错误关键字"""
    strategy = RetryStrategy()
    
    # timeout 应该重试
    error = LLMError("Request timeout")
    assert strategy.should_retry(error, 0) is True
    
    # connection 应该重试
    error = LLMError("Connection error")
    assert strategy.should_retry(error, 0) is True
    
    # rate limit 应该重试
    error = LLMError("Rate limit exceeded")
    assert strategy.should_retry(error, 0) is True


def test_should_not_retry_after_max_attempts():
    """测试达到最大重试次数后不再重试"""
    strategy = RetryStrategy(max_retries=3)
    
    error = LLMError("Server error", status_code=500)
    
    # 前 3 次应该重试
    assert strategy.should_retry(error, 0) is True
    assert strategy.should_retry(error, 1) is True
    assert strategy.should_retry(error, 2) is True
    
    # 第 4 次不应该重试
    assert strategy.should_retry(error, 3) is False


def test_get_delay_with_exponential_backoff():
    """测试指数退避延迟计算"""
    strategy = RetryStrategy(
        max_retries=5,
        base_delay=1.0,
        exponential_base=2.0,
        jitter=False,  # 禁用抖动以便测试
    )
    
    # 第 1 次: 1.0 * 2^0 = 1.0
    delay = strategy.get_delay(0)
    assert delay == 1.0
    
    # 第 2 次: 1.0 * 2^1 = 2.0
    delay = strategy.get_delay(1)
    assert delay == 2.0
    
    # 第 3 次: 1.0 * 2^2 = 4.0
    delay = strategy.get_delay(2)
    assert delay == 4.0


def test_get_delay_with_retry_after():
    """测试使用服务端指定的 retry_after"""
    strategy = RetryStrategy(jitter=False)  # 禁用抖动以便测试
    
    error = LLMError("Rate limit", status_code=429, retry_after=5.0)
    
    # 应该使用 retry_after 而不是指数退避
    delay = strategy.get_delay(0, error)
    assert delay == 5.0


def test_get_delay_with_max_delay():
    """测试最大延迟限制"""
    strategy = RetryStrategy(
        max_retries=10,
        base_delay=1.0,
        max_delay=10.0,
        jitter=False,
    )
    
    # 即使指数退避计算结果很大，也不应该超过 max_delay
    delay = strategy.get_delay(10)
    assert delay <= 10.0


def test_get_delay_with_jitter():
    """测试随机抖动"""
    strategy = RetryStrategy(
        base_delay=1.0,
        jitter=True,
    )
    
    # 多次获取延迟，应该有一定随机性
    delays = [strategy.get_delay(0) for _ in range(10)]
    
    # 延迟应该在 0.5 * base_delay 到 1.5 * base_delay 之间
    for delay in delays:
        assert 0.5 <= delay <= 1.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
