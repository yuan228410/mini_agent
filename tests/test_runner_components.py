"""测试 runner 模块核心组件"""
import pytest
from mini_ai.runner.state import LoopState
from mini_ai.runner.error_handler import ErrorCategory, ErrorHandler, ErrorMessage
from mini_ai.exceptions import (
    MiniAIError,
    ToolError,
    LLMError,
    ConfigError,
)


class TestLoopState:
    """测试 LoopState 状态管理"""
    
    def test_default_values(self):
        """默认值正确"""
        state = LoopState()
        
        assert state.turn == 0
        assert state.max_turns == 20
        assert state.consecutive_errors == 0
        assert state.spawned_teammate is False
        assert state.messages == []
    
    def test_should_continue(self):
        """should_continue 逻辑正确"""
        state = LoopState(max_turns=3)
        
        assert state.should_continue() is True  # turn=0 < 3
        
        state.increment_turn()
        assert state.should_continue() is True  # turn=1 < 3
        
        state.increment_turn()
        state.increment_turn()
        assert state.should_continue() is False  # turn=3 >= 3
    
    def test_increment_turn(self):
        """轮次递增正确"""
        state = LoopState()
        
        state.increment_turn()
        assert state.turn == 1
        
        state.increment_turn()
        assert state.turn == 2
    
    def test_error_tracking(self):
        """错误计数正确"""
        state = LoopState()
        
        state.record_error()
        assert state.consecutive_errors == 1
        
        state.record_error()
        assert state.consecutive_errors == 2
        
        state.clear_errors()
        assert state.consecutive_errors == 0
    
    def test_mark_spawned(self):
        """spawn 标记正确"""
        state = LoopState()
        
        assert state.spawned_teammate is False
        
        state.mark_spawned()
        assert state.spawned_teammate is True
    
    def test_stats(self):
        """stats 返回正确统计"""
        state = LoopState(max_turns=10)
        state.increment_turn()
        state.record_error()
        state.mark_spawned()
        state.messages.append({"role": "user", "content": "test"})
        
        stats = state.stats()
        
        assert stats["turn"] == 1
        assert stats["max_turns"] == 10
        assert stats["consecutive_errors"] == 1
        assert stats["spawned_teammate"] is True
        assert stats["message_count"] == 1


class TestErrorHandler:
    """测试 ErrorHandler 错误处理"""
    
    def test_handle_config_error(self):
        """配置错误应终止"""
        handler = ErrorHandler()
        state = LoopState()
        
        error = ConfigError("缺少 API key")
        result = handler.handle(error, state)
        
        assert result is not None
        assert result["role"] == "user"
        assert result["error_category"] == ErrorCategory.CONFIG.value
        assert "配置错误" in result["content"]
    
    def test_handle_llm_rate_limit(self):
        """LLM 速率限制错误"""
        handler = ErrorHandler()
        state = LoopState()
        
        error = LLMError("Rate limit", status_code=429)
        result = handler.handle(error, state)
        
        assert result is not None
        assert result["error_category"] == ErrorCategory.LLM_RATE_LIMIT.value
        assert "频繁" in result["content"]
    
    def test_handle_llm_server_error(self):
        """LLM 服务器错误"""
        handler = ErrorHandler()
        state = LoopState()
        
        error = LLMError("Server error", status_code=500)
        result = handler.handle(error, state)
        
        assert result is not None
        assert result["error_category"] == ErrorCategory.LLM.value
        assert "LLM 调用失败" in result["content"]
    
    def test_handle_tool_error_single(self):
        """单次工具错误"""
        handler = ErrorHandler(max_consecutive_errors=3)
        state = LoopState()
        
        error = ToolError("read_file", "File not found")
        result = handler.handle(error, state)
        
        assert result is not None
        assert result["error_category"] == ErrorCategory.TOOL.value
        assert "工具执行失败" in result["content"]
        assert state.consecutive_errors == 1
    
    def test_handle_tool_error_consecutive(self):
        """连续工具错误达到上限"""
        handler = ErrorHandler(max_consecutive_errors=3)
        state = LoopState()
        
        # 连续 3 次错误
        for _ in range(3):
            error = ToolError("read_file", "Failed")
            result = handler.handle(error, state)
        
        assert state.consecutive_errors == 3
        assert result["error_category"] == ErrorCategory.TOOL_CONSECUTIVE.value
        assert "连续执行失败" in result["content"]
    
    def test_handle_unknown_error(self):
        """未知异常处理"""
        handler = ErrorHandler()
        state = LoopState()
        
        error = ValueError("Unexpected")
        result = handler.handle(error, state)
        
        assert result is not None
        assert result["error_category"] == ErrorCategory.UNKNOWN.value
        assert "未知错误" in result["content"]
        assert "ValueError" in result["content"]
    
    def test_should_terminate_config_error(self):
        """配置错误应终止循环"""
        handler = ErrorHandler()
        
        error = ConfigError("Bad config")
        assert handler.should_terminate(error) is True
    
    def test_should_terminate_other_errors(self):
        """其他错误不应终止循环"""
        handler = ErrorHandler()
        
        assert handler.should_terminate(ToolError("read_file", "fail")) is False
        assert handler.should_terminate(LLMError("error")) is False
        assert handler.should_terminate(ValueError("oops")) is False
    
    def test_error_message_has_timestamp(self):
        """错误消息包含时间戳"""
        handler = ErrorHandler()
        state = LoopState()

        error = ToolError("read_file", "Failed")
        result = handler.handle(error, state)

        assert "timestamp" in result
        assert len(result["timestamp"]) == 19  # YYYY-MM-DDTHH:MM:SS

    def test_error_message_dto_to_runtime_message(self):
        """错误消息 DTO 显式转换为 runtime MessageDict"""
        msg = ErrorMessage(ErrorCategory.TOOL, "工具失败", "2026-01-01T00:00:00").to_message()

        assert msg == {
            "role": "user",
            "content": "工具失败",
            "timestamp": "2026-01-01T00:00:00",
            "error_category": "tool",
        }


class TestErrorHandlerIntegration:
    """错误处理器集成测试"""
    
    def test_error_count_resets_on_success(self):
        """成功操作后错误计数应重置（通过 clear_errors）"""
        handler = ErrorHandler(max_consecutive_errors=3)
        state = LoopState()
        
        # 记录 2 次错误
        state.record_error()
        state.record_error()
        assert state.consecutive_errors == 2
        
        # 清除错误
        state.clear_errors()
        assert state.consecutive_errors == 0
        
        # 再次错误不应触发"连续失败"消息
        error = ToolError("read_file", "Failed again")
        result = handler.handle(error, state)
        
        assert "连续执行失败" not in result["content"]
