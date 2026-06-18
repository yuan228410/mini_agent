"""消息持久化器 — 统一 CLI/Web 的 DB 写入逻辑

HistoryPersister 封装 tool/assistant/deferred-assistant 三种消息的持久化，
消除 main.py 和 chat.py 的双写。
"""
from __future__ import annotations
import json

from ..logger import logger
from ..plan.artifact_parser import strip_artifact_blocks


class HistoryPersister:
    """统一消息持久化逻辑，CLI 和 Web 共用

    Usage:
        persister = HistoryPersister(history_db, workspace, session_id)
        # 作为 persist_fn 回调传给 run_tool_loop
        msg, _ = run_tool_loop(messages, tools, persist_fn=persister, ...)
        # run_tool_loop 结束后，flush deferred assistant（含 tool_calls._result）
        persister.flush_deferred(messages)
    """

    def __init__(self, history_db, workspace: str, session_id: str, sanitize_plan_artifacts: bool = False):
        self._db = history_db
        self._ws = workspace
        self._sid = session_id
        self._sanitize_plan_artifacts = sanitize_plan_artifacts
        self._deferred_assistant: list[dict] = []

    def __call__(self, msg: dict) -> None:
        """persist_fn 回调：根据 role 写入 DB

        - tool 消息：立即写入
        - assistant 消息（无 tool_calls）：立即写入
        - assistant 消息（有 tool_calls）：延迟写入（等 _result 回填后一起写）
        """
        if msg["role"] == "tool":
            self._db.append(
                self._ws, self._sid, "tool", msg.get("content", ""),
                metadata=json.dumps({
                    "name": msg.get("name", ""),
                    "tool_call_id": msg.get("tool_call_id", ""),
                }),
            )
        elif msg["role"] == "assistant":
            if msg.get("tool_calls"):
                self._deferred_assistant.append(msg)
            else:
                meta = {}
                if msg.get("thinking"):
                    meta["thinking"] = msg["thinking"]
                if msg.get("kind"):
                    meta["kind"] = msg["kind"]
                if msg.get("plan"):
                    meta["plan"] = msg["plan"]
                content = msg.get("content", "")
                if self._sanitize_plan_artifacts:
                    content = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
                elif msg.get("kind") == "plan_discussion":
                    content = strip_artifact_blocks(content)
                self._db.append(
                    self._ws, self._sid, "assistant", content,
                    metadata=json.dumps(meta) if meta else "",
                )

    def flush_deferred(self, messages: list[dict]) -> None:
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
            if am.get("tool_calls"):
                for tc in am["tool_calls"]:
                    tc_id = tc.get("id", "")
                    result = tool_results.get(tc_id, "")
                    tc["_result"] = result
                    enriched_tcs.append(tc)

            meta = {}
            if am.get("thinking"):
                meta["thinking"] = am["thinking"]
            if am.get("kind"):
                meta["kind"] = am["kind"]
            if am.get("plan"):
                meta["plan"] = am["plan"]
            if enriched_tcs:
                meta["tool_calls"] = enriched_tcs

            content = am.get("content", "")
            if self._sanitize_plan_artifacts:
                content = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
            elif am.get("kind") == "plan_discussion":
                content = strip_artifact_blocks(content)
            self._db.append(
                self._ws, self._sid, "assistant", content,
                metadata=json.dumps(meta) if meta else "",
            )

        self._deferred_assistant.clear()

    @property
    def has_deferred(self) -> bool:
        """是否有待 flush 的 deferred assistant"""
        return bool(self._deferred_assistant)
