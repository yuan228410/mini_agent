"""Web 端 Display 适配器 — 将事件推入 WebSocket 队列（线程安全）"""
import asyncio
import time

class WebDisplay:
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop
        self._thinking_buf = ""
        self._thinking_start_time = 0.0
        self._tool_start_time = 0.0
        self._last_thinking = ""
        self._had_thinking = False
        self._stream_buf = ""
        self._streaming = False
        self.thinking_mode = "collapsed"
        self.tool_detail = "summary"

    def _push(self, event: str, data: dict | None = None):
        from mini_ai.llm import get_usage
        usage = get_usage()
        if data is None:
            data = {}
        data["prompt_tokens"] = usage["prompt_tokens"]
        data["completion_tokens"] = usage["completion_tokens"]
        self.loop.call_soon_threadsafe(
            lambda: self.queue.put_nowait({"event": event, "data": data})
        )

    def thinking_start(self):
        self._thinking_buf = ""
        self._thinking_start_time = time.monotonic()
        self._push("thinking_start")

    def thinking_chunk(self, text: str):
        self._thinking_buf += text
        self._push("thinking", {"content": text})

    def thinking_end(self):
        elapsed = time.monotonic() - self._thinking_start_time
        n_chars = len(self._thinking_buf)
        if self._thinking_buf:
            self._last_thinking = self._thinking_buf
        self._had_thinking = True
        self._push("thinking_end", {"chars": n_chars, "elapsed": round(elapsed, 1)})
        self._thinking_buf = ""

    def text_chunk(self, text: str):
        self._stream_buf += text
        self._streaming = True
        self._push("text", {"content": text})

    def text_end(self, full_text: str | None = None):
        if full_text:
            self._stream_buf = ""
            self._streaming = True
            self._push("text", {"content": full_text})
        self._stream_buf = ""
        self._streaming = False
        self._had_thinking = False

    def tool_call_start(self, name: str, args_summary: str):
        self._tool_start_time = time.monotonic()
        self._push("tool_start", {"name": name, "args": args_summary[:200]})

    def tool_result(self, name: str, result: str, elapsed: float | None = None):
        if elapsed is None:
            elapsed = time.monotonic() - self._tool_start_time
        if result.startswith("📋TODO\n"):
            self._push("todos", {"content": result[6:]})
            self._push("tool_result", {"name": name, "result": result[:200], "elapsed": round(elapsed, 1)})
            return
        self._push("tool_result", {"name": name, "result": result[:500], "elapsed": round(elapsed, 1)})

    def assistant_prefix(self):
        pass

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
