"""消息持久化器 — 统一 CLI/Web 的 DB 写入逻辑

HistoryPersister 封装 tool/assistant/deferred-assistant 三种消息的持久化，
消除 main.py 和 chat.py 的双写。
"""
from __future__ import annotations
import json

from ..plan.artifact_parser import strip_artifact_blocks
from .messages import ChatMessage, MessageRole
from .runtime_types import HistoryDBProtocol, MessageDict
from .tool_models import ToolCall, ToolResult


class HistoryPersister:
    """统一消息持久化逻辑，CLI 和 Web 共用

    Usage:
        persister = HistoryPersister(history_db, workspace, session_id)
        # 作为 persist_fn 回调传给 run_tool_loop
        msg, _ = run_tool_loop(messages, tools, persist_fn=persister, ...)
        # run_tool_loop 结束后，flush deferred assistant（含 tool_calls._result）
        persister.flush_deferred(messages)
    """

    def __init__(self, history_db: HistoryDBProtocol, workspace: str, session_id: str, sanitize_plan_artifacts: bool = False):
        self._db = history_db
        self._ws = workspace
        self._sid = session_id
        self._sanitize_plan_artifacts = sanitize_plan_artifacts
        self._deferred_assistant: list[ChatMessage] = []

    def _assistant_persistence_payload(self, msg: ChatMessage, *, tool_calls: list[ToolCall] | None = None) -> tuple[str, str]:
        """Return DB content/metadata for an assistant message via DTO fields."""
        meta: dict = {}
        if msg.extra.get("thinking"):
            meta["thinking"] = msg.extra["thinking"]
        if msg.extra.get("kind"):
            meta["kind"] = msg.extra["kind"]
        if msg.extra.get("plan"):
            meta["plan"] = msg.extra["plan"]
        if tool_calls:
            meta["tool_calls"] = [tc.to_dict(include_result=True) for tc in tool_calls]

        content = msg.content or ""
        if self._sanitize_plan_artifacts:
            content = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
        elif msg.extra.get("kind") == "plan_discussion":
            content = strip_artifact_blocks(content)
        return content, json.dumps(meta, ensure_ascii=False) if meta else ""

    def __call__(self, msg: MessageDict) -> None:
        """persist_fn 回调：根据 role 写入 DB

        - tool 消息：立即写入
        - assistant 消息（无 tool_calls）：立即写入
        - assistant 消息（有 tool_calls）：延迟写入（等 _result 回填后一起写）
        """
        chat_msg = ChatMessage.from_dict(msg)
        if chat_msg.role is MessageRole.TOOL:
            tool_result = ToolResult.from_message(chat_msg.to_dict())
            self._db.append(
                self._ws, self._sid, MessageRole.TOOL.value, tool_result.content,
                metadata=json.dumps({
                    "name": tool_result.name,
                    "tool_call_id": tool_result.tool_call_id,
                }, ensure_ascii=False),
            )
        elif chat_msg.role is MessageRole.ASSISTANT:
            if chat_msg.tool_calls:
                self._deferred_assistant.append(chat_msg)
            else:
                content, metadata = self._assistant_persistence_payload(chat_msg)
                self._db.append(self._ws, self._sid, MessageRole.ASSISTANT.value, content, metadata=metadata)

    def flush_deferred(self, messages: list[MessageDict]) -> None:
        """将 deferred assistant 消息的 _result 回填并持久化

        在 run_tool_loop 结束后调用。遍历 _deferred_assistant，
        从 messages 中找到对应的 tool 消息回填 _result，然后写入 DB。
        """
        if not self._deferred_assistant:
            return

        # 构建 tool_call_id → tool_content 映射
        tool_results: dict[str, str] = {}
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id"):
                tool_results[m["tool_call_id"]] = m.get("content", "")

        for am in self._deferred_assistant:
            enriched_tcs = []
            for tc in am.tool_calls:
                result = tool_results.get(tc.id, "")
                enriched_tcs.append(ToolCall(
                    id=tc.id,
                    function=tc.function,
                    type=tc.type,
                    result_preview=result,
                    extra=dict(tc.extra),
                ))

            content, metadata = self._assistant_persistence_payload(am, tool_calls=enriched_tcs)
            self._db.append(self._ws, self._sid, MessageRole.ASSISTANT.value, content, metadata=metadata)

        self._deferred_assistant.clear()

    @property
    def has_deferred(self) -> bool:
        """是否有待 flush 的 deferred assistant"""
        return bool(self._deferred_assistant)
