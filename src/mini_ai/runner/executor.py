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
from ..core.display_protocol import DisplayProtocol

class ToolExecutor:
    """工具执行器
    
    负责：
    - 调用 LLM（流式/非流式）
    - 处理工具调用
    - 管理显示回调
    """
    
    def __init__(
        self,
        display: DisplayProtocol | None = None,
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
        from ..llm import chat as llm_chat, get_model
        
        # 调用开始
        model = get_model(ctx)
        if self.display:
            self.display.llm_round_start(model)
        
        try:
            msg = llm_chat(messages, tools=tools, ctx=ctx)
        except Exception as e:
            # 非流式溢出时 LLM 抛 LLMError，需要先清理 display 状态
            if self.display:
                self.display.text_end()
            raise
        
        # 调用结束
        if self.display:
            from ..llm import get_usage
            usage = get_usage()
            self.display.llm_round_end(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                model=model
            )
        
        # 处理 thinking
        if msg and msg.get("thinking") and self.display:
            self.display.thinking_full(msg["thinking"])
        
        return msg
    
    def _call_llm_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        ctx: Any,
        abort_event: threading.Event | None,
    ) -> dict | None:
        """流式调用 LLM（支持自动重试）"""
        from ..llm import chat_stream as llm_chat_stream, get_model, get_usage
        from ..llm.retry import RetryStrategy
        from ..config import TIMEOUTS
        
        # 初始化重试策略（与 LLM 层保持一致）
        max_retries = TIMEOUTS.get("llm_retries", 3)
        retry_delay = TIMEOUTS.get("llm_retry_delay", 2)
        strategy = RetryStrategy(
            max_retries=max_retries,
            base_delay=retry_delay,
            max_delay=60.0,
        )
        
        last_error = None
        for attempt in range(max_retries + 1):
            # 调用开始
            model = get_model(ctx)
            if self.display:
                self.display.llm_round_start(model)
            
            msg = None
            thinking_seen = False
            last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            stream_error = None
            stream_overflow = False
            
            for chunk in llm_chat_stream(messages, tools=tools, ctx=ctx, abort_event=abort_event):
                if abort_event and abort_event.is_set():
                    # 🔧 修复：中断时返回已生成的内容（如果有）
                    if self.display:
                        partial_content = self.display.text_end()
                        if partial_content:
                            # 返回部分消息，让上层决定是否保存
                            return {
                                "role": "assistant",
                                "content": partial_content,
                                "interrupted": True,
                            }
                    else:
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
                elif chunk_type == "usage":
                    # 捕获 usage 信息
                    last_usage["prompt_tokens"] = chunk.get("prompt_tokens", 0)
                    last_usage["completion_tokens"] = chunk.get("completion_tokens", 0)
                elif chunk_type == "error":
                    stream_error = chunk.get("error")
                    stream_overflow = chunk.get("is_context_overflow", False)
                    logger.error(f"[LLM✗] 流式错误: {stream_error}")
                    # 不立即返回，等重试逻辑处理
                    break
                elif chunk_type == "done":
                    msg = chunk.get("msg")
                    # 🔧 诊断日志：记录 msg 内容
                    if msg:
                        content_preview = (msg.get("content") or "")[:100]
                        has_tool_calls = bool(msg.get("tool_calls"))
                        logger.debug(f"[Executor] done chunk: content={content_preview}, tool_calls={has_tool_calls}")
                    else:
                        logger.warning(f"[Executor⚠] done chunk 但 msg 为 None, last_usage={last_usage}")
            
            # 如果成功完成，返回结果
            if stream_error is None:
                if thinking_seen and self.display:
                    self.display.thinking_end()
                
                # 🔧 修复：检查是否收到有效响应
                if msg is None and last_usage["completion_tokens"] == 0:
                    # 没有收到任何内容，可能是网络问题
                    stream_error = "流式响应为空（可能因网络中断或服务端错误）"
                    logger.warning(f"[LLM⚠] {stream_error}, attempt={attempt}")
                    # 继续重试逻辑
                else:
                    # 调用结束
                    if self.display:
                        usage = get_usage()
                        self.display.llm_round_end(
                            prompt_tokens=usage.get("prompt_tokens", last_usage["prompt_tokens"]),
                            completion_tokens=usage.get("completion_tokens", last_usage["completion_tokens"]),
                            model=model
                        )
                    
                    return msg
            
            # 流式错误或无响应，尝试重试
            from ..exceptions import LLMError
            last_error = LLMError(stream_error)
            
            if strategy.should_retry(last_error, attempt):
                delay = strategy.get_delay(attempt, last_error)
                logger.warning(f"[LLM↻] 流式重试 {attempt+1}/{max_retries}: {stream_error}，{delay:.1f}s 后重试")
                
                # 结束当前显示
                if self.display:
                    self.display.text_end()
                
                time.sleep(delay)
                # 继续下一次循环（重试）
            else:
                # 不可重试或达到最大重试次数
                logger.error(f"[LLM✗] 流式错误(已重试{attempt}次): {stream_error}")
                if self.display:
                    self.display.text_end()
                # 返回带错误标记的消息（error 由 chat_runner 统一通过 complete 事件发送）
                return {
                    "role": "assistant",
                    "content": None,
                    "error": stream_error,
                    "is_context_overflow": stream_overflow,
                }
        
        # 所有重试都失败
        logger.error(f"[LLM✗] 流式错误(已重试{max_retries}次): {last_error}")
        if self.display:
            self.display.text_end()
        # 返回带错误标记的消息（error 由 chat_runner 统一通过 complete 事件发送）
        return {
            "role": "assistant",
            "content": None,
            "error": str(last_error),
            "is_context_overflow": stream_overflow,
        }
    
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
