"""测试 runner 核心逻辑"""
import pytest
import threading
from unittest.mock import patch, MagicMock

from mini_ai.runner import run_tool_loop, run_agent


class FakeToolRegistry:
    def __init__(self, handler=None, definitions=None):
        self.handler = handler or (lambda msg, messages, **kwargs: False)
        self.definitions = definitions or []
        self.call_count = 0

    def get_definitions(self):
        return self.definitions

    def handle_tool_calls(self, msg, messages, **kwargs):
        self.call_count += 1
        return self.handler(msg, messages, **kwargs)


class TestRunToolLoop:
    """测试工具循环"""
    
    def test_simple_response_no_tools(self):
        """无工具调用的简单响应"""
        messages = [{"role": "user", "content": "Hello"}]
        
        mock_response = {"role": "assistant", "content": "Hi there!"}
        
        with patch("mini_ai.llm.chat") as mock_chat:
            mock_chat.return_value = mock_response
            
            result, spawned = run_tool_loop(
                messages,
                tools=None,
                streaming=False,
                max_turns=5,
                tool_registry=FakeToolRegistry(),
            )
            
            assert result is not None
            assert result["content"] == "Hi there!"
            assert spawned is False
    
    def test_tool_call_and_continue(self):
        """工具调用后继续循环"""
        messages = [{"role": "user", "content": "Read the README"}]
        
        # 第一轮：返回工具调用
        tool_call_response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "/tmp/README.md"}'
                }
            }]
        }
        # 第二轮：返回最终响应
        final_response = {"role": "assistant", "content": "The README says..."}
        
        with patch("mini_ai.llm.chat") as mock_chat:
            mock_chat.side_effect = [tool_call_response, final_response]
            
            registry = FakeToolRegistry()
            result, spawned = run_tool_loop(
                messages,
                tools=[],
                streaming=False,
                max_turns=5,
                tool_registry=registry,
            )

            assert result is not None
            assert mock_chat.call_count == 2
            assert registry.call_count == 1
    
    def test_max_turns_limit(self):
        """达到最大轮次限制"""
        messages = [{"role": "user", "content": "Keep calling tools"}]
        
        # 持续返回工具调用
        tool_response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'}
            }]
        }
        
        with patch("mini_ai.llm.chat") as mock_chat:
            # 前 3 次返回工具调用，第 4 次返回总结
            mock_chat.side_effect = [tool_response] * 3 + [{"content": "Summary"}]
            
            result, spawned = run_tool_loop(
                messages,
                tools=[],
                streaming=False,
                max_turns=3,
                tool_registry=FakeToolRegistry(),
            )

            # 应该在达到 max_turns 后强制退出
            assert result is not None
    
    def test_abort_on_event(self):
        """通过 abort_event 中断循环"""
        messages = [{"role": "user", "content": "Long task"}]
        abort_event = threading.Event()
        
        # 预先设置 abort_event，模拟用户中断
        abort_event.set()
        
        with patch("mini_ai.llm.chat") as mock_chat:
            mock_chat.return_value = {"content": "Response"}
            
            result, spawned = run_tool_loop(
                messages,
                tools=None,
                streaming=False,
                abort_event=abort_event,
                max_turns=10,
                tool_registry=FakeToolRegistry(),
            )
            
            assert result is None  # 被中断
            assert mock_chat.call_count == 0  # 未调用 LLM
    
    def test_consecutive_tool_errors(self):
        """连续工具错误后提前退出"""
        messages = [{"role": "user", "content": "Test errors"}]
        
        tool_response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "/missing"}'}
            }]
        }
        
        with patch("mini_ai.llm.chat") as mock_chat:
            mock_chat.side_effect = [tool_response] * 5 + [{"content": "I failed"}]
            
            # 模拟工具执行失败：在 messages 中添加错误 tool 消息
            def add_error_msg(msg, messages, **kwargs):
                messages.append({
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "Error: File not found"
                })
                return False

            registry = FakeToolRegistry(handler=add_error_msg)
            result, spawned = run_tool_loop(
                messages,
                tools=[],
                streaming=False,
                max_turns=10,
                tool_registry=registry,
            )

            # 连续 3 次错误后应退出
            assert registry.call_count <= 4


class TestRunAgent:
    """测试轻量 agent 循环"""
    
    def test_run_agent_returns_content(self):
        """run_agent 返回文本内容"""
        messages = [{"role": "user", "content": "Hello"}]
        
        with patch("mini_ai.runner.loop.run_tool_loop") as mock_loop:
            mock_loop.return_value = ({"content": "Hi!"}, False)
            
            result = run_agent(messages, max_turns=5, tool_registry=FakeToolRegistry())
            
            assert result == "Hi!"
    
    def test_run_agent_no_content_finds_last_assistant(self):
        """无内容时查找最后一条 assistant 消息"""
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Previous response"},
            {"role": "user", "content": "Continue"}
        ]
        
        with patch("mini_ai.runner.loop.run_tool_loop") as mock_loop:
            mock_loop.return_value = (None, False)
            
            result = run_agent(messages, max_turns=5, tool_registry=FakeToolRegistry())
            
            assert result == "Previous response"
