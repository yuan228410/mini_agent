"""测试配置和 fixtures"""
import os
import sys
import json
import tempfile
from pathlib import Path
from typing import Generator
from dataclasses import dataclass, field

import pytest


# ═══════════════════════════════════════════
# 测试目录配置
# ═══════════════════════════════════════════

TEST_DIR = Path(__file__).parent


# ═══════════════════════════════════════════
# Mock LLM 响应
# ═══════════════════════════════════════════

@dataclass
class MockLLMResponse:
    """Mock LLM 响应配置"""
    content: str = "Mock response"
    tool_calls: list[dict] = field(default_factory=list)
    thinking: str = ""
    
    def to_dict(self) -> dict:
        result = {"role": "assistant", "content": self.content}
        if self.thinking:
            result["thinking"] = self.thinking
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        return result


@pytest.fixture
def mock_llm():
    """Mock LLM 调用"""
    responses: list[MockLLMResponse] = []
    call_count = [0]
    
    def _chat(messages, tools=None, ctx=None):
        call_count[0] += 1
        if responses:
            return responses.pop(0).to_dict()
        return MockLLMResponse().to_dict()
    
    def _chat_stream(messages, tools=None, ctx=None, abort_event=None):
        """Mock 流式响应"""
        call_count[0] += 1
        response = responses.pop(0) if responses else MockLLMResponse()
        yield {"type": "text", "content": response.content}
        yield {"type": "done", "msg": response.to_dict()}
    
    def _add_response(resp: MockLLMResponse):
        responses.append(resp)
    
    def _get_call_count():
        return call_count[0]
    
    # 返回控制对象
    return type("MockLLM", (), {
        "chat": _chat,
        "chat_stream": _chat_stream,
        "add_response": _add_response,
        "call_count": property(lambda self: call_count[0]),
    })()


# ═══════════════════════════════════════════
# 临时工作空间
# ═══════════════════════════════════════════

@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """创建临时目录"""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def temp_workspace(temp_dir: Path) -> Path:
    """创建临时工作空间（含基本目录结构）"""
    ws = temp_dir / "workspace"
    ws.mkdir()
    
    # 创建项目文件
    (ws / "README.md").write_text("# Test Project\n\nThis is a test.")
    (ws / "main.py").write_text("print('hello')")
    
    # 创建子目录
    src_dir = ws / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")
    (src_dir / "app.py").write_text("def main(): pass")
    
    return ws


@pytest.fixture
def temp_config(temp_dir: Path) -> Generator[Path, None, None]:
    """创建临时配置文件"""
    config_content = """
active_model: test-model
models:
  test-model:
    api_url: https://api.test.com/v1
    api_key: test-key
    model: test-model-name
    context_length: 128000
streaming: false
compactor:
  keep_recent: 50
"""
    config_path = temp_dir / "config.yaml"
    config_path.write_text(config_content)
    yield config_path


# ═══════════════════════════════════════════
# Memory Store Fixtures
# ═══════════════════════════════════════════

@pytest.fixture
def temp_memory_store(temp_dir: Path):
    """创建临时 MemoryStore"""
    from mini_ai.memory import MemoryStore
    store_dir = temp_dir / "memory_data"
    return MemoryStore(store_dir)


# ═══════════════════════════════════════════
# Tool Fixtures
# ═══════════════════════════════════════════

@pytest.fixture
def tool_registry():
    """获取工具注册表"""
    from mini_ai.tools import get_registry
    return get_registry()


# ═══════════════════════════════════════════
# 配置隔离
# ═══════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_config():
    """每个测试后重置配置"""
    import mini_ai.config as config_module
    original_raw = config_module._raw.copy() if config_module._raw else {}
    yield
    # 恢复原始配置
    config_module._raw = original_raw
    if original_raw:
        config_module._apply_config(original_raw)


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def assert_tool_call(msg: dict, tool_name: str) -> dict:
    """断言消息包含指定工具调用，返回工具参数"""
    assert "tool_calls" in msg, f"消息没有 tool_calls: {msg}"
    for tc in msg["tool_calls"]:
        if tc["function"]["name"] == tool_name:
            return json.loads(tc["function"]["arguments"])
    raise AssertionError(f"未找到工具调用 {tool_name}")


def create_tool_call(tool_name: str, args: dict, call_id: str = "call_123") -> dict:
    """创建工具调用消息"""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(args)
        }
    }
