"""Web 端 Display 适配器 — 将事件推入 WebSocket 队列（线程安全）"""
import asyncio
import time
import threading

# 全局事件序号计数器（每个会话独立）
_EVENT_SEQS: dict[str, int] = {}
_EVENT_SEQS_LOCK = threading.Lock()

class WebDisplay:
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, session_id: str = "", agent_id: str = ""):
        self.queue = queue
        self.loop = loop
        self.session_id = session_id  # 会话ID，用于前端路由工作流事件
        self.agent_id = agent_id  # Agent ID，用于前端分组消息
        self._teammate = ""
        self._thinking_buf = ""
        self._thinking_start_time = 0.0
        self._tool_start_time = 0.0
        self._last_thinking = ""
        self._had_thinking = False
        self._stream_buf = ""
        self._streaming = False
        self.thinking_mode = "collapsed"
        self.tool_detail = "summary"
        self._llm_round_start_time = 0.0

    def set_teammate(self, name: str):
        self._teammate = name

    def set_agent_id(self, agent_id: str):
        self.agent_id = agent_id

    def _push(self, event: str, data: dict | None = None):
        from mini_ai.llm import get_usage
        usage = get_usage()
        if data is None:
            data = {}
        # 自动注入 session_id（用于前端路由工作流事件到对应会话）
        if self.session_id:
            data["session_id"] = self.session_id
        # 自动注入 agent_id（用于前端分组消息）
        if self.agent_id:
            data["agent_id"] = self.agent_id
        data["promptTokens"] = usage["prompt_tokens"]
        data["completionTokens"] = usage["completion_tokens"]
        
        # 添加事件序号（用于前端检测消息丢失）
        if self.session_id:
            with _EVENT_SEQS_LOCK:
                seq = _EVENT_SEQS.get(self.session_id, 0) + 1
                _EVENT_SEQS[self.session_id] = seq
                data["seq"] = seq
        
        self.loop.call_soon_threadsafe(
            lambda: self.queue.put_nowait({"event": event, "data": data})
        )

    def llm_round_start(self, model: str = ""):
        """LLM 调用开始"""
        self._llm_round_start_time = time.monotonic()
        data = {"model": model}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("llm_round_start", data)

    def llm_round_end(self, prompt_tokens: int = 0, completion_tokens: int = 0, model: str = ""):
        """LLM 调用结束"""
        elapsed = time.monotonic() - self._llm_round_start_time
        data = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "elapsed": round(elapsed, 2),
            "model": model,
        }
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("llm_round_end", data)

    def thinking_start(self):
        self._thinking_buf = ""
        self._thinking_start_time = time.monotonic()
        data: dict = {}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("thinking_start", data)

    def thinking_chunk(self, text: str):
        self._thinking_buf += text
        data = {"content": text}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("thinking", data)

    def thinking_end(self):
        elapsed = time.monotonic() - self._thinking_start_time
        n_chars = len(self._thinking_buf)
        if self._thinking_buf:
            self._last_thinking = self._thinking_buf
        self._had_thinking = True
        data = {"chars": n_chars, "elapsed": round(elapsed, 1)}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("thinking_end", data)
        self._thinking_buf = ""

    def text_chunk(self, text: str):
        self._stream_buf += text
        self._streaming = True
        data = {"content": text}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("text", data)

    def text_end(self, full_text: str | None = None):
        # 🔧 修复：先保存 _stream_buf，再清空（避免中断时丢失）
        saved_buf = self._stream_buf
        if full_text:
            self._stream_buf = ""
            self._streaming = True
            data = {"content": full_text}
            if self._teammate:
                data["teammate"] = self._teammate
            self._push("text", data)
        else:
            # 如果没有 full_text，说明是中断或异常结束，保留 saved_buf 供上层保存
            pass
        self._stream_buf = ""
        self._streaming = False
        self._had_thinking = False
        # 返回保存的内容，供中断时使用
        return saved_buf

    def tool_call_start(self, name: str, args_summary: str, tool_call_id: str = ""):
        self._tool_start_time = time.monotonic()
        data = {"name": name, "args": args_summary, "tool_call_id": tool_call_id}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push("tool_start", data)

    def tool_result(self, name: str, result: str, elapsed: float | None = None, tool_call_id: str = ""):
        if elapsed is None:
            elapsed = time.monotonic() - self._tool_start_time
        if result.startswith("📋TODO\n"):
            self._push("todos", {"content": result[6:]})
            rdata = {"name": name, "result": result, "elapsed": round(elapsed, 1), "tool_call_id": tool_call_id}
            if self._teammate: rdata["teammate"] = self._teammate
            self._push("tool_result", rdata)
            return
        rdata = {"name": name, "result": result, "elapsed": round(elapsed, 1), "tool_call_id": tool_call_id}
        if self._teammate: rdata["teammate"] = self._teammate
        self._push("tool_result", rdata)

    def assistant_prefix(self):
        pass

    def teammate_status(self, name: str, status: str):
        self._push("teammate_status", {"name": name, "status": status})

    def blackboard_update(self, key: str, author: str):
        self._push("blackboard_update", {"key": key, "author": author})

    def inbox_message(self, to: str, from_user: str, count: int):
        self._push("inbox_message", {"to": to, "from": from_user, "count": count})

    def info(self, text: str):
        pass

    def error(self, text: str):
        pass

    def show_banner(self):
        pass

    def user_input(self) -> str:
        return ""

    def status_bar(self, **kwargs):
        pass

    def show_thinking(self):
        pass

    def set_thinking_mode(self, mode: str):
        self.thinking_mode = mode
