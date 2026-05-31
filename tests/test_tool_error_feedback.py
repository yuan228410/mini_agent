"""测试工具异常反馈给 LLM"""
import pytest
from unittest.mock import Mock, patch
import json


class TestToolErrorFeedback:
    """测试工具执行异常是否正确反馈给 LLM"""
    
    def test_json_parse_error_feedback(self):
        """JSON 解析错误应返回详细错误信息"""
        from mini_ai.tools import ToolRegistry
        
        registry = ToolRegistry()
        
        # 构造无效 JSON 参数的工具调用
        tc = {
            "id": "call_123",
            "function": {
                "name": "read_file",
                "arguments": "{invalid json"
            }
        }
        
        messages = []
        registry._execute_one(tc, messages)
        
        # 应该有一条 tool 消息
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "call_123"
        
        # 错误信息应包含详细内容
        content = messages[0]["content"]
        assert "⚠" in content
        assert "JSON 解析错误" in content
        assert "read_file" in content
    
    def test_tool_exception_feedback(self):
        """工具执行异常应返回详细错误信息"""
        from mini_ai.tools import ToolRegistry
        
        registry = ToolRegistry()
        
        # Mock 一个会抛出异常的工具
        def mock_execute(args):
            raise FileNotFoundError(f"文件不存在: {args.get('path')}")
        
        mock_tool = Mock()
        mock_tool.definition = {
            "type": "function",
            "function": {
                "name": "test_tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }
        mock_tool.execute = mock_execute
        
        registry._tools = [mock_tool]
        registry._rebuild_index()
        
        tc = {
            "id": "call_456",
            "function": {
                "name": "test_tool",
                "arguments": json.dumps({"path": "/nonexistent/file.txt"})
            }
        }
        
        messages = []
        registry._execute_one(tc, messages)
        
        assert len(messages) == 1
        content = messages[0]["content"]
        
        # 应包含详细错误信息
        assert "⚠" in content
        assert "文件操作失败" in content
        assert "test_tool" in content
        assert "FileNotFoundError" in content
    
    def test_parallel_tool_error_feedback(self):
        """并行工具执行异常应返回详细错误信息"""
        from mini_ai.tools import ToolRegistry
        
        registry = ToolRegistry()
        registry._parallel_tools.add("error_tool")
        
        # Mock 会抛出异常的工具
        def mock_execute(args):
            raise PermissionError("权限不足")
        
        mock_tool = Mock()
        mock_tool.definition = {
            "type": "function",
            "function": {
                "name": "error_tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }
        mock_tool.execute = mock_execute
        
        registry._tools = [mock_tool]
        registry._rebuild_index()
        
        calls = [
            {
                "id": "call_789",
                "function": {
                    "name": "error_tool",
                    "arguments": json.dumps({"path": "/root/secret"})
                }
            }
        ]
        
        messages = []
        registry._execute_parallel(calls, messages)
        
        assert len(messages) == 1
        content = messages[0]["content"]
        
        # 应包含详细错误信息
        assert "⚠" in content
        assert "文件操作失败" in content
        assert "PermissionError" in content
    
    def test_mini_ai_exception_feedback(self):
        """MiniAI 异常应使用 to_user_message()"""
        from mini_ai.tools import ToolRegistry
        from mini_ai.exceptions import ToolError, ResourceNotFoundError
        
        registry = ToolRegistry()
        
        # Mock 会抛出 ResourceNotFoundError 的工具
        def mock_execute(args):
            raise ResourceNotFoundError("read_file", args.get("path", ""))
        
        mock_tool = Mock()
        mock_tool.definition = {
            "type": "function",
            "function": {
                "name": "test_tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }
        mock_tool.execute = mock_execute
        
        registry._tools = [mock_tool]
        registry._rebuild_index()
        
        tc = {
            "id": "call_abc",
            "function": {
                "name": "test_tool",
                "arguments": json.dumps({"path": "/missing/file.txt"})
            }
        }
        
        messages = []
        registry._execute_one(tc, messages)
        
        assert len(messages) == 1
        content = messages[0]["content"]
        
        # 应包含 MiniAI 异常的详细信息
        assert "⚠" in content
        assert "test_tool" in content
        assert "ResourceNotFoundError" in content or "资源不存在" in content
    
    def test_unknown_exception_includes_traceback(self):
        """未知异常应包含完整堆栈"""
        from mini_ai.tools import ToolRegistry
        
        registry = ToolRegistry()
        
        # Mock 会抛出未知异常的工具
        def mock_execute(args):
            raise RuntimeError("Unexpected error")
        
        mock_tool = Mock()
        mock_tool.definition = {
            "type": "function",
            "function": {
                "name": "test_tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }
        mock_tool.execute = mock_execute
        
        registry._tools = [mock_tool]
        registry._rebuild_index()
        
        tc = {
            "id": "call_def",
            "function": {
                "name": "test_tool",
                "arguments": "{}"
            }
        }
        
        messages = []
        registry._execute_one(tc, messages)
        
        assert len(messages) == 1
        content = messages[0]["content"]
        
        # 应包含堆栈信息
        assert "⚠" in content
        assert "RuntimeError" in content
        assert "堆栈" in content


class TestErrorDetectionPattern:
    """测试错误检测模式"""
    
    def test_old_error_format_detected(self):
        """旧格式 'Error:' 应被检测为错误"""
        from mini_ai.runner.loop import _has_recent_tool_error
        
        messages = [
            {"role": "tool", "content": "Error: 文件不存在"}
        ]
        
        assert _has_recent_tool_error(messages) is True
    
    def test_new_error_format_detected(self):
        """新格式 '⚠' 应被检测为错误"""
        from mini_ai.runner.loop import _has_recent_tool_error
        
        messages = [
            {"role": "tool", "content": "⚠ 文件操作失败\n\n工具: read_file"}
        ]
        
        assert _has_recent_tool_error(messages) is True
    
    def test_success_not_detected_as_error(self):
        """成功结果不应被检测为错误"""
        from mini_ai.runner.loop import _has_recent_tool_error
        
        messages = [
            {"role": "tool", "content": "文件读取成功\n内容: ..."}
        ]
        
        assert _has_recent_tool_error(messages) is False
