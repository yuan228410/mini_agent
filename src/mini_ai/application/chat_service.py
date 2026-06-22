"""Application-level chat turn orchestration shared by adapters."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.display_protocol import DisplayProtocol
from ..core.persister import HistoryPersister
from ..core.runtime_context import SessionIdentity, SessionRuntimeContext
from ..core.runtime_factory import build_request_context, build_session_runtime
from ..core.runtime_types import (
    HistoryDBProtocol,
    MessageBusProtocol,
    MessageDict,
    PlanStateStoreProtocol,
    RequestContextProtocol,
    ToolDefinition,
    UsageDict,
)
from ..core.settings import ModelSettings, SettingsSnapshot
from ..llm import chat as llm_chat, get_usage, reset_usage
from ..plan.artifact_parser import strip_artifact_blocks
from ..plan.prompts import build_plan_user_message
from ..plan.service import PlanService
from ..plan.store import PlanStore
from ..plan.tool_policy import ToolPolicy, filter_tools
from ..runner import run_tool_loop
from ..tools import inject_todos as _inject_todos
from ..utils import now_ts
from .chat_export import ChatExportResult, export_chat
from .chat_history import chat_history
from .chat_response import (
    PLAN_DISCUSSION_DISPLAY_CONTENT,
    TEAM_FOLLOWUP_INSTRUCTION,
    append_chat_assistant_message,
    apply_plan_discussion_response,
    chat_error_context,
    fallback_error_text,
    finalize_chat_assistant_response,
    handle_invalid_chat_result,
    inject_team_inbox_messages,
    should_poll_team_followup,
    team_followup_timing,
    valid_assistant_result,
)
from .chat_turn import (
    append_user_message,
    build_chat_runtime_bundle,
    build_user_message,
    default_chat_tools,
    ensure_session_system_prompt,
    persist_latest_user_message,
    persisted_user_payload,
    prepare_chat_turn,
    prepare_execution_turn,
    prepare_plan_turn,
    select_turn_tools,
    visible_user_messages,
)
from .chat_models import (
    ChatAssistantDispatch,
    ChatErrorResult,
    ChatPreparedTurn,
    ChatRuntimeBundle,
    RunTurnOptions,
    RunTurnResult,
    TeamFollowupTiming,
    TeamInboxInjectionResult,
)


@dataclass(frozen=True, slots=True)
class ChatRestDependencies:
    session_manager: Any
    cache_key: Callable[[str, str | None, str], str]
    resolve_base: Callable[[str, str | None], Path]
    get_or_create_session: Callable[..., tuple[str, list[MessageDict] | None]]
    get_or_create_components: Callable[[str, str, Path | None, str | None], dict[str, Any]]
    load_session_name: Callable[[Path | None, str], str]
    update_meta_cache: Callable[[str, str, str | None, list[MessageDict] | None], None]
    inject_todos: Callable[[list[MessageDict]], None]


@dataclass(frozen=True, slots=True)
class ChatRuntimeDependencies:
    build_system_prompt: Callable[[str, str, Path | None, str | None], str]
    settings_for_model: Callable[[SettingsSnapshot, str | None], SettingsSnapshot]
    build_runtime: Callable[..., SessionRuntimeContext]
    subagent_loader: Any
    mcp_loader: Any


PLAN_DISCUSSION_DISPLAY_CONTENT = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
TEAM_FOLLOWUP_INSTRUCTION = "队友回禀已收到。请先 blackboard_read 获取队友写入黑板的结果，再基于回禀和黑板内容回复用户。"


def reset_chat(deps: ChatRestDependencies, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reset an in-memory chat session to its system prompt."""

    payload = body or {}
    username = payload.get("username", "")
    session_id = payload.get("session_id", "") or "default"
    workspace = payload.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}

    ws = workspace or None
    base = deps.resolve_base(username, ws)
    sid, messages = deps.get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    system_content = messages[0]["content"]
    old_name = messages[0].get("name", "")
    key = deps.cache_key(username, ws, sid)

    deps.session_manager.reset_session(key, system_content, old_name)
    msgs = deps.session_manager.get_messages(key)
    if msgs:
        deps.inject_todos(msgs)
    deps.update_meta_cache(username, sid, ws, msgs)

    return {"status": "ok", "session_id": sid}


class ApplicationService:
    """Central turn runner used by CLI/Web adapters.

    Adapters own transport concerns (terminal input, WebSocket queues,
    active-task locks), while this service owns the common LLM/tool/persist/plan
    flow.
    """

    def __init__(self, *, default_settings: SettingsSnapshot | None = None, request_context_factory=None):
        self._default_settings = default_settings
        self._request_context_factory = request_context_factory

    def _fallback_settings(self) -> SettingsSnapshot:
        return self._default_settings or SettingsSnapshot(model=ModelSettings.from_dict(None))

    def _new_request_context(self, settings: SettingsSnapshot, display: DisplayProtocol | None) -> RequestContextProtocol:
        if self._request_context_factory is None:
            return build_request_context(settings, display=display)
        return self._request_context_factory(model_config=settings.model.to_dict(), display=display, timeout_settings=settings.timeouts)

    def run_turn(
        self,
        *,
        messages: list[MessageDict] | None = None,
        tools: list[ToolDefinition] | None = None,
        history_db: HistoryDBProtocol | None = None,
        workspace: str | None = None,
        session_id: str | None = None,
        compactor=None,
        bus: MessageBusProtocol | None = None,
        plan_store: PlanStore | None = None,
        plan_state: PlanStateStoreProtocol | None = None,
        user_text_for_history: str | None = None,
        options: RunTurnOptions | None = None,
        runtime: SessionRuntimeContext | None = None,
    ) -> RunTurnResult:
        settings = runtime.settings if runtime is not None else self._fallback_settings()
        if runtime is not None:
            messages = runtime.messages
            history_db = runtime.history_db
            workspace = runtime.identity.workspace
            session_id = runtime.identity.session_id
            compactor = runtime.compactor
            bus = runtime.bus
            options = options or RunTurnOptions()
            options.display = options.display if options.display is not None else runtime.tool_context.display
            options.request_context = options.request_context or runtime.request_context
            options.tool_registry = options.tool_registry or runtime.tool_registry
            options.streaming = options.streaming if options.streaming is not None else settings.streaming
            options.context_length = options.context_length if options.context_length is not None else settings.model.context_length
            options.max_turns = options.max_turns or settings.runner.max_turns
        options = options or RunTurnOptions()
        if options.streaming is None:
            options.streaming = settings.streaming
        if options.context_length is None:
            options.context_length = settings.model.context_length
        if messages is None or tools is None or history_db is None or workspace is None or session_id is None:
            raise ValueError("ApplicationService.run_turn requires messages/tools/history_db/workspace/session_id or runtime")
        if options.tool_registry is None:
            raise ValueError("ApplicationService.run_turn requires a session-local tool_registry")
        ctx = options.request_context
        owns_ctx = ctx is None
        if ctx is None:
            ctx = self._new_request_context(settings, options.display)

        raw_plan_text = None
        persister = HistoryPersister(history_db, workspace, session_id, sanitize_plan_artifacts=options.plan_turn)
        try:
            user_msgs = [m for m in messages if m.get("role") == "user" and not m.get("_internal")]
            if options.persist_user_history and user_msgs:
                last_user = user_msgs[-1]
                meta = {k: v for k, v in last_user.items() if k not in ("role", "content", "timestamp", "_plan_original_content")}
                content = user_text_for_history if user_text_for_history is not None else last_user.get("_plan_original_content", last_user.get("content", ""))
                history_db.append(workspace, session_id, "user", content, metadata=json.dumps(meta) if meta else "")

            plan_session_key = options.plan_session_key or session_id

            if options.plan_turn:
                tools = filter_tools(tools, ToolPolicy.PLAN_READONLY)
            else:
                tools = filter_tools(tools, ToolPolicy.EXECUTION)
                if options.approved_plan:
                    PlanService().seed_execution_todos(artifact=options.approved_plan, session_key=plan_session_key, display=options.display)
                    messages.append({"role": "user", "content": PlanService().execution_instruction(options.approved_plan), "timestamp": now_ts(), "_internal": True})

            reset_usage()
            msg, _ = run_tool_loop(
                messages,
                tools,
                streaming=options.streaming,
                display=options.display,
                inject_fn=_inject_todos,
                abort_event=options.abort_event,
                max_turns=options.max_turns,
                ctx=ctx,
                persist_fn=persister,
                bus=bus,
                context_length=options.context_length,
                compactor=compactor,
                tool_registry=options.tool_registry,
                timeout_settings=settings.timeouts if settings else None,
                max_consecutive_errors=int(settings.runner.extra.get("max_consecutive_errors", 3)) if settings else 3,
            )
            persister.flush_deferred(messages)

            if options.plan_turn and msg and msg.get("content"):
                raw_plan_text = msg["content"]
                msg["content"] = strip_artifact_blocks(raw_plan_text) or "计划已更新。"
                msg["kind"] = "plan_discussion"
                if plan_store:
                    PlanService().update_from_response(
                        session_key=plan_session_key,
                        sm=plan_state,
                        store=plan_store,
                        user_text=user_text_for_history or "",
                        assistant_text=raw_plan_text,
                        display=options.display,
                    )
            elif options.approved_plan and plan_store:
                PlanService().mark_completed(session_key=plan_session_key, sm=plan_state, store=plan_store, display=options.display)

            usage = dict(get_usage())
            if runtime is not None and runtime.usage_collector is not None:
                runtime.usage_collector.set(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                )
            if compactor:
                compactor.maybe_compact(messages, usage["prompt_tokens"], llm_chat, ctx, options.context_length)

            return RunTurnResult(message=msg, usage=usage, raw_plan_text=raw_plan_text)
        finally:
            if owns_ctx:
                ctx.close()
