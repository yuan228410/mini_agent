"""工具执行器

封装 LLM 调用和工具执行逻辑，提供：
- 流式/非流式 LLM 调用
- 工具调用处理
- 显示回调
"""
import time
import threading
from typing import Any, Callable

from ..logger import logger
from ..utils import now_ts

class ToolExecutor:
    """工具执行器
    
    负责：
    - 调用 LLM（流式/非流式）
    - 处理工具调用
    - 管理显示回调
    """
    
    def __init__(
        self,
        display: Any = None,
        persist_fn: Callable[[dict], None] | None = None,
        streaming: bool = False,
    ):
        """
        Args:
            display: 显示回调对象
            persist_fn: 消息持久化函数
            streaming: 是否流式输出
        """
        self.display = display
        self.persist_fn = persist_fn or (lambda m: None)
        self.streaming = streaming
    
    def call_llm(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        ctx: Any = None,
        abort_event: threading.Event | None = None,
    ) -> dict | None:
        """调用 LLM（根据 streaming 选择模式）
        
        Args:
            messages: 消息列表
            tools: 工具定义
            ctx: 请求上下文
            abort_event: 中断事件
        
        Returns:
            LLM 响应消息
        """
        if self.streaming:
            return self._call_llm_stream(messages, tools, ctx, abort_event)
        else:
            return self._call_llm_sync(messages, tools, ctx)
    
    def _call_llm_sync(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        ctx: Any,
    ) -> dict | None:
        """同步调用 LLM"""
        from ..llm import chat as llm_chat
        
        msg = llm_chat(messages, tools=tools, ctx=ctx)
        
        # 处理 thinking
        if msg and msg.get("thinking") and self.display:
            self.display.thinking_start()
            self.display._thinking_buf = msg["thinking"]
            self.display.thinking_end()
        
        return msg
    
    def _call_llm_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        ctx: Any,
        abort_event: threading.Event | None,
    ) -> dict | None:
        """流式调用 LLM"""
        from ..llm import chat_stream as llm_chat_stream
        
        msg = None
        thinking_seen = False
        
        for chunk in llm_chat_stream(messages, tools=tools, ctx=ctx, abort_event=abort_event):
            if abort_event and abort_event.is_set():
                if self.display:
                    self.display.text_end()
                return None
            
            chunk_type = chunk.get("type")
            
            if chunk_type == "thinking_start":
                if self.display:
                    self.display.thinking_start()
                thinking_seen = True
            elif chunk_type == "thinking":
                if self.display:
                    self.display.thinking_chunk(chunk.get("content", ""))
            elif chunk_type == "thinking_end":
                if self.display:
                    self.display.thinking_end()
                thinking_seen = False
            elif chunk_type == "text":
                if self.display:
                    self.display.text_chunk(chunk.get("content", ""))
            elif chunk_type == "error":
                logger.error(f"[LLM✗] 流式错误: {chunk.get('error')}")
                if self.display:
                    self.display.text_end()
                    self.display.tool_result("error", f"⚠ LLM 错误: {chunk.get('error')}", elapsed=0)
                return None
            elif chunk_type == "done":
                msg = chunk.get("msg")
        
        if thinking_seen and self.display:
            self.display.thinking_end()
        
        return msg
    
    def execute_tools(
        self,
        msg: dict,
        messages: list[dict],
        state: Any = None,
    ) -> bool:
        """执行工具调用
        
        Args:
            msg: LLM 响应消息（含 tool_calls）
            messages: 消息列表（会被修改）
            state: 循环状态
        
        Returns:
            是否 spawn 了队友
        """
        from ..tools import handle_tool_calls
        
        display = self.display
        spawned = handle_tool_calls(msg, messages, display=display, persist_fn=self.persist_fn)
        
        return spawned
    
    def finalize_response(
        self,
        msg: dict | None,
        messages: list[dict],
    ) -> None:
        """完成响应（添加时间戳、持久化）
        
        Args:
            msg: 响应消息
            messages: 消息列表
        """
        from datetime import datetime, timezone, timedelta
        
        timestamp = now_ts()
        
        if msg:
            msg["timestamp"] = timestamp
            self.persist_fn(msg)
            
            if msg.get("content") and self.display:
                self.display.text_end(msg["content"])
