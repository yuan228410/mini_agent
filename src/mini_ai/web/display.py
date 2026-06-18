"""Web 端 Display 适配器 — 将事件推入 WebSocket 队列（线程安全）"""
import asyncio
import time
import threading
from typing import Any

from ..core import events
from ..core.events import DisplayEvent, DisplayEventType, TERMINAL_EVENT_TYPES
from ..team.models import WorkflowTaskEnd, WorkflowTaskInfo, WorkflowTaskStart

# 全局事件序号计数器（每个会话独立）
_EVENT_SEQS: dict[str, int] = {}
_EVENT_SEQS_LOCK = threading.Lock()
_TERMINAL_EVENTS = {e.value for e in TERMINAL_EVENT_TYPES}
_MAX_PENDING_EVENTS = 2000


def cleanup_session_seq(session_id: str):
    """清理会话事件序号，避免长生命周期 Web 服务累积。"""
    if not session_id:
        return
    with _EVENT_SEQS_LOCK:
        _EVENT_SEQS.pop(session_id, None)

class WebDisplay:
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, session_id: str = "", agent_id: str = "", suppress_text: bool = False):
        self.queue = queue
        self.loop = loop
        self.session_id = session_id  # 会话ID，用于前端路由工作流事件
        self.agent_id = agent_id  # Agent ID，用于前端分组消息
        self.suppress_text = suppress_text  # 计划模式下先缓存原始输出，解析后只发送渲染友好的摘要
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
        self._pending_events: list[dict] = []
        self._pending_lock = threading.Lock()
        self._flush_scheduled = False

    def set_teammate(self, name: str):
        self._teammate = name

    def set_agent_id(self, agent_id: str):
        self.agent_id = agent_id

    def child(self, *, agent_id: str = "", teammate: str = "", suppress_text: bool | None = None):
        child = WebDisplay(
            self.queue,
            self.loop,
            session_id=self.session_id,
            agent_id=agent_id or self.agent_id,
            suppress_text=self.suppress_text if suppress_text is None else suppress_text,
        )
        child.thinking_mode = self.thinking_mode
        child.tool_detail = self.tool_detail
        if teammate:
            child.set_teammate(teammate)
        return child

    def emit(self, event: str | DisplayEvent, data: dict[str, Any] | None = None):
        if isinstance(event, DisplayEvent):
            self._push(event.event.value, event.data)
        else:
            self._push(event, data)

    def _push(self, event: str | DisplayEventType, data: dict | None = None):
        if isinstance(event, DisplayEventType):
            event = event.value
        from mini_ai.llm import get_global_usage
        usage = get_global_usage()
        if event in _TERMINAL_EVENTS:
            from ..logger import logger as _dlog
            _dlog.info(f'[Display] push: event={event} sid={self.session_id} has_err={bool(data and data.get("error"))}')
        if data is None:
            data = {}
        # 自动注入 session_id（用于前端路由工作流事件到对应会话）
        if self.session_id:
            data["session_id"] = self.session_id
        # 自动注入 agent_id（用于前端分组消息）
        if self.agent_id:
            data["agent_id"] = self.agent_id
        data.setdefault("prompt_tokens", usage["prompt_tokens"])
        data.setdefault("completion_tokens", usage["completion_tokens"])
        
        # 添加事件序号（用于前端检测消息丢失）
        if self.session_id:
            with _EVENT_SEQS_LOCK:
                seq = _EVENT_SEQS.get(self.session_id, 0) + 1
                _EVENT_SEQS[self.session_id] = seq
                data["seq"] = seq
        
        self._enqueue({"event": event, "data": data})

    def _enqueue(self, item: dict):
        with self._pending_lock:
            if len(self._pending_events) >= _MAX_PENDING_EVENTS:
                if item.get("event") in _TERMINAL_EVENTS:
                    # 为终止事件腾位置：优先丢弃最旧的非终止事件。
                    for i, pending in enumerate(self._pending_events):
                        if pending.get("event") not in _TERMINAL_EVENTS:
                            self._pending_events.pop(i)
                            break
                    else:
                        self._pending_events.pop(0)
                else:
                    self._pending_events.pop(0)
            self._pending_events.append(item)
            if self._flush_scheduled:
                return
            self._flush_scheduled = True
        try:
            self.loop.call_soon_threadsafe(self._flush_pending)
        except Exception:
            with self._pending_lock:
                self._flush_scheduled = False

    def _put_with_priority(self, item: dict):
        try:
            self.queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            if item.get("event") not in _TERMINAL_EVENTS:
                return False
            # 终止事件不允许静默丢弃，丢弃一个旧的非终止事件后重试。
            try:
                buffered = []
                dropped = False
                while True:
                    old = self.queue.get_nowait()
                    if not dropped and old.get("event") not in _TERMINAL_EVENTS:
                        dropped = True
                        continue
                    buffered.append(old)
            except asyncio.QueueEmpty:
                pass
            for old in buffered:
                try:
                    self.queue.put_nowait(old)
                except asyncio.QueueFull:
                    break
            try:
                self.queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                return False
        except Exception:
            return False

    def _flush_pending(self):
        from ..logger import logger as _dlog
        with self._pending_lock:
            pending = self._pending_events
            self._pending_events = []
            self._flush_scheduled = False
        dropped = 0
        for item in pending:
            if not self._put_with_priority(item):
                dropped += 1
        if dropped:
            _dlog.warning(f"[Display] dropped {dropped} low-priority events sid={self.session_id}")

    def llm_round_start(self, model: str = ""):
        """LLM 调用开始"""
        self._llm_round_start_time = time.monotonic()
        data = {"model": model}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push(DisplayEventType.LLM_ROUND_START, data)

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
        self._push(DisplayEventType.LLM_ROUND_END, data)

    def thinking_start(self):
        self._thinking_buf = ""
        self._thinking_start_time = time.monotonic()
        data: dict = {}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push(DisplayEventType.THINKING_START, data)

    def thinking_chunk(self, text: str):
        self._thinking_buf += text
        data = {"content": text}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push(DisplayEventType.THINKING, data)

    def thinking_end(self):
        elapsed = time.monotonic() - self._thinking_start_time
        n_chars = len(self._thinking_buf)
        if self._thinking_buf:
            self._last_thinking = self._thinking_buf
        self._had_thinking = True
        data = {"chars": n_chars, "elapsed": round(elapsed, 1)}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push(DisplayEventType.THINKING_END, data)
        self._thinking_buf = ""

    def thinking_full(self, text: str):
        self.thinking_start()
        if text:
            self.thinking_chunk(text)
        self.thinking_end()

    def text_chunk(self, text: str):
        self._stream_buf += text
        self._streaming = True
        if self.suppress_text:
            return
        data = {"content": text}
        if self._teammate:
            data["teammate"] = self._teammate
        self._push(DisplayEventType.TEXT, data)

    def text_end(self, full_text: str | None = None):
        # 🔧 修复：先保存 _stream_buf，再清空（避免中断时丢失）
        saved_buf = self._stream_buf
        # 流式过程中已通过 text_chunk 发送分段，前端累加成完整文本。
        # 这里仅在以下两种情况补发 text 事件：
        #   1) 非流式路径（_stream_buf 为空）：full_text 是唯一来源，必须发
        #   2) full_text 与已累积内容不一致：兜底，避免丢失内容
        # 否则跳过补发，否则前端会把完整文本再追加一次，导致显示两遍。
        should_emit = bool(full_text) and not self.suppress_text and (not saved_buf or full_text != saved_buf)
        if should_emit:
            self._stream_buf = ""
            self._streaming = True
            data = {"content": full_text}
            if self._teammate:
                data["teammate"] = self._teammate
            self._push(DisplayEventType.TEXT, data)
        else:
            # 正常流式结束或中断：保留 saved_buf 供上层使用
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
        self._push(DisplayEventType.TOOL_START, data)

    def tool_result(self, name: str, result: str, elapsed: float | None = None, tool_call_id: str = ""):
        if elapsed is None:
            elapsed = time.monotonic() - self._tool_start_time
        rdata = {"name": name, "result": result, "elapsed": round(elapsed, 1), "tool_call_id": tool_call_id}
        if self._teammate: rdata["teammate"] = self._teammate
        self._push(DisplayEventType.TOOL_RESULT, rdata)

    def assistant_prefix(self):
        pass

    def teammate_status(self, name: str, status: str):
        self._push(DisplayEventType.TEAMMATE_STATUS, {"name": name, "status": status})

    def blackboard_update(self, key: str, author: str):
        self._push(DisplayEventType.BLACKBOARD_UPDATE, {"key": key, "author": author})

    def inbox_message(self, to: str, from_user: str, count: int):
        self._push(DisplayEventType.INBOX_MESSAGE, {"to": to, "from": from_user, "count": count})

    def info(self, text: str):
        self._push(DisplayEventType.INFO, {"message": text})

    def plan_event(self, kind: str, **data):
        payload = {"kind": kind}
        payload.update(data)
        self._push(DisplayEventType.PLAN_EVENT, payload)

    def todos_updated(self, content: str):
        self._push(DisplayEventType.TODOS, {"content": content})

    def agent_start(self, agent_type: str, task: str = "", role: str = "", max_turns: int | None = None):
        payload = {"agent_type": agent_type}
        if task:
            payload["task"] = task
        if role:
            payload["role"] = role
        if max_turns is not None:
            payload["max_turns"] = max_turns
        if self._teammate:
            payload["teammate"] = self._teammate
        self._push(DisplayEventType.AGENT_START, payload)

    def workflow_start(self, tasks: list[WorkflowTaskInfo | dict[str, Any]], total: int):
        self.emit(events.workflow_start(tasks, total))

    def workflow_task_start(self, task_id: str, agent: str, prompt: str):
        self.emit(events.workflow_task_start(task_id, agent, prompt))

    def workflow_task_start_event(self, task: WorkflowTaskStart):
        self.emit(events.workflow_task_start_event(task))

    def workflow_task_end(self, task_id: str, status: str, result_preview: str | None = None, error: str | None = None):
        self.emit(events.workflow_task_end(task_id, status, result_preview=result_preview, error=error))

    def workflow_task_end_event(self, task: WorkflowTaskEnd):
        self.emit(events.workflow_task_end_event(task))

    def workflow_end(self, elapsed: float, completed: int, failed: int, total: int):
        self.emit(events.workflow_end(elapsed, completed, failed, total))

    def error(self, text: str):
        self._push(DisplayEventType.ERROR, {"error": text})

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
