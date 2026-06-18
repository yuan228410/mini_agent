"""统一会话运行逻辑 — CLI/Web 共用

ChatSession 封装一轮对话的完整流程：
  user append → run_tool_loop → 压缩 → 队友等待 → 持久化
消除 main.py 和 chat.py 的双写。
"""
from __future__ import annotations
import json
import threading
import time

from ..config import MODEL_CONFIG, STREAMING, PLAN, TIMEOUTS
from ..llm import get_usage, reset_usage, chat as llm_chat
from ..logger import logger
from ..runner import run_tool_loop
from ..tools import inject_todos as _inject_todos
from ..utils import now_ts
from .persister import HistoryPersister


class ChatSession:
    """统一会话运行逻辑

    Usage (CLI):
        session = ChatSession(messages, compactor, history_db, ...)
        msg = session.run(user_input, tools, streaming=True, display=disp, ctx=ctx)

    Usage (Web):
        # Web 端仍用 chat_runner.py 的 run_tool_loop_sync
        # （因为 Web 有额外的 queue/display/abort 逻辑）
        # 但压缩和持久化逻辑已统一
    """

    def __init__(self, messages: list[dict], compactor, history_db,
                 bus=None, team_mgr=None, lead_event: threading.Event | None = None,
                 context_length: int = 256000,
                 workspace: str = "", session_id: str = "",
                 tool_registry=None):
        self.messages = messages
        self.compactor = compactor
        self.history_db = history_db
        self.bus = bus
        self.team_mgr = team_mgr
        self.lead_event = lead_event
        self.context_length = context_length
        self.workspace = workspace
        self.session_id = session_id
        self.tool_registry = tool_registry
        self._persister = HistoryPersister(history_db, workspace, session_id)

    def run(self, user_input: str, tools: list[dict],
            *, streaming: bool = False, display=None, inject_fn=None,
            abort_event: threading.Event | None = None,
            max_turns: int = 0, ctx=None,
            persist_fn=None,
            tool_registry=None) -> dict | None:
        """执行一轮对话

        Args:
            user_input: 用户输入文本
            tools: 工具定义列表
            streaming: 是否流式输出
            display: Display 适配器
            inject_fn: 消息注入函数
            abort_event: 中断事件
            max_turns: 最大轮次
            ctx: 请求上下文
            persist_fn: 自定义持久化函数（若提供则覆盖内置 persister）

        Returns:
            LLM 最终响应消息
        """
        # append user message
        ts = now_ts()
        self.messages.append({"role": "user", "content": user_input, "timestamp": ts})

        # 持久化 user message
        self.history_db.append(self.workspace, self.session_id, "user", user_input)

        # 选择 persist_fn
        pfn = persist_fn or self._persister

        reset_usage()
        msg, _ = run_tool_loop(
            self.messages, tools,
            streaming=streaming,
            display=display,
            inject_fn=inject_fn or _inject_todos,
            abort_event=abort_event,
            max_turns=max_turns,
            ctx=ctx,
            persist_fn=pfn,
            bus=self.bus,
            context_length=self.context_length,
            compactor=self.compactor,
            tool_registry=tool_registry or self.tool_registry,
        )

        # flush deferred assistant
        if pfn is self._persister:
            self._persister.flush_deferred(self.messages)

        return msg

    def maybe_compact(self, ctx=None) -> bool:
        """检查并执行日常压缩

        Returns:
            True = 执行了压缩
        """
        usage = get_usage()
        return self.compactor.maybe_compact(
            self.messages, usage["prompt_tokens"],
            llm_chat, ctx, self.context_length
        )

    def wait_teammates(self, tools, display=None, ctx=None,
                        abort_event: threading.Event | None = None) -> dict | None:
        """等待队友回禀

        Returns:
            队友消息（如有），或 None
        """
        if not self.bus or not self.team_mgr:
            return None

        from ..team.loop import wait_for_teammates, format_inbox_messages, cleanup_inbox

        teammate_msg = wait_for_teammates(
            self.bus, self.team_mgr, self.lead_event,
            self._run_loop_fn, self.messages, tools,
            _inject_todos, display,
            history_db=self.history_db, ctx=ctx,
            workspace=self.workspace, session_id=self.session_id,
        )

        cleanup_inbox(self.bus)
        return teammate_msg

    def _run_loop_fn(self, messages, tools, **kwargs):
        """供 wait_for_teammates 调用的 run_tool_loop 封装"""
        return run_tool_loop(messages, tools, **kwargs)

    def persist_assistant(self, msg: dict):
        """持久化 assistant 消息（含 thinking）"""
        if msg and msg.get("content"):
            ts = now_ts()
            self.messages.append({
                "role": "assistant",
                "content": msg["content"],
                "thinking": msg.get("thinking"),
                "timestamp": ts,
            })
            meta = {}
            if msg.get("thinking"):
                meta["thinking"] = msg["thinking"]
            self.history_db.append(
                self.workspace, self.session_id,
                "assistant", msg.get("content", ""),
                metadata=json.dumps(meta) if meta else ""
            )
