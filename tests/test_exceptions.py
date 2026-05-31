"""测试异常体系"""
import pytest

from mini_ai.exceptions import (
    MiniAIError,
    ConfigError,
    LLMError,
    ToolError,
    ResourceNotFoundError,
    PermissionDeniedError,
    ValidationError,
    TeamError,
    WorkflowError,
)


class TestMiniAIError:
    """测试基础异常类"""
    
    def test_basic_error(self):
        """基本错误创建"""
        err = MiniAIError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.recoverable is True
        assert err.context == {}
    
    def test_error_with_context(self):
        """带上下文的错误"""
        err = MiniAIError(
            "File not found",
            recoverable=False,
            file_path="/tmp/test.txt",
            operation="read"
        )
        assert err.recoverable is False
        assert err.context["file_path"] == "/tmp/test.txt"
        assert "context=" in str(err)
    
    def test_user_message(self):
        """自定义用户消息"""
        err = MiniAIError(
            "Internal error code 500",
            user_message="服务暂时不可用，请稍后重试"
        )
        assert err.to_user_message() == "服务暂时不可用，请稍后重试"


class TestConfigError:
    """测试配置错误"""
    
    def test_missing_field(self):
        """缺少必填字段"""
        err = ConfigError(
            "缺少必填字段 api_key",
            config_path="/path/to/config.yaml",
            field="api_key"
        )
        assert err.recoverable is False
        assert err.config_path == "/path/to/config.yaml"
        assert err.field == "api_key"


class TestLLMError:
    """测试 LLM 错误"""
    
    def test_rate_limit(self):
        """速率限制错误"""
        err = LLMError(
            "Rate limit exceeded",
            provider="openai",
            status_code=429,
            retry_after=60.0
        )
        assert err.recoverable is True
        assert err.status_code == 429
        assert err.retry_after == 60.0
        assert "请求过于频繁" in err.to_user_message()
    
    def test_server_error(self):
        """服务器错误"""
        err = LLMError(
            "Internal server error",
            provider="anthropic",
            status_code=500
        )
        assert "服务暂时不可用" in err.to_user_message()
    
    def test_generic_error(self):
        """通用错误"""
        err = LLMError(
            "Connection timeout",
            provider="openai",
            model="gpt-4"
        )
        assert "LLM 调用失败" in err.to_user_message()


class TestToolError:
    """测试工具错误"""
    
    def test_basic_tool_error(self):
        """基本工具错误"""
        err = ToolError("read_file", "Failed to read file")
        assert err.tool_name == "read_file"
        assert "[read_file]" in str(err)
        assert err.recoverable is True
    
    def test_resource_not_found(self):
        """资源不存在错误"""
        err = ResourceNotFoundError(
            "read_file",
            "/tmp/missing.txt",
            resource_type="文件"
        )
        assert err.tool_name == "read_file"
        assert err.resource == "/tmp/missing.txt"
        assert "文件不存在" in err.to_user_message()
    
    def test_permission_denied(self):
        """权限不足错误"""
        err = PermissionDeniedError(
            "write_file",
            "/etc/passwd",
            action="写入"
        )
        assert err.recoverable is False
        assert "无权限写入" in str(err)
    
    def test_validation_error(self):
        """参数校验错误"""
        err = ValidationError(
            "run_command",
            "command",
            "不能为空",
            expected="非空字符串",
            actual=""
        )
        assert err.param_name == "command"
        assert err.recoverable is True


class TestTeamError:
    """测试 Team 错误"""
    
    def test_teammate_error(self):
        """队友错误"""
        err = TeamError(
            "Teammate timeout",
            teammate_name="coder"
        )
        assert err.teammate_name == "coder"
        assert err.recoverable is True


class TestWorkflowError:
    """测试工作流错误"""
    
    def test_task_error(self):
        """任务错误"""
        err = WorkflowError(
            "Task dependency failed",
            task_id="deploy"
        )
        assert err.task_id == "deploy"
        assert err.recoverable is True
