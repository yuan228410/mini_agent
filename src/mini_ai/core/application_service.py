"""Application-level session orchestration shared by adapters."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from ..config import MODEL_CONFIG, STREAMING, RequestContext
from ..llm import chat as llm_chat, get_usage, reset_usage
from ..plan.artifact_parser import strip_artifact_blocks
from ..plan.service import PlanService
from ..plan.store import PlanStore
from ..plan.tool_policy import ToolPolicy, filter_tools
from ..runner import run_tool_loop
from ..tools import inject_todos as _inject_todos
from ..utils import now_ts
from .display_protocol import DisplayProtocol
from .persister import HistoryPersister
from .runtime_context import SessionRuntimeContext
from .runtime_types import HistoryDBProtocol, MessageBusProtocol, MessageDict, PlanStateStoreProtocol, RequestContextProtocol, ToolDefinition, ToolRegistryProtocol


@dataclass
class RunTurnOptions:
    streaming: bool | None = None
    display: DisplayProtocol | None = None
    request_context: RequestContextProtocol | None = None
    abort_event: threading.Event | None = None
    max_turns: int = 0
    plan_turn: bool = False
    approved_plan: MessageDict | None = None
    tool_registry: ToolRegistryProtocol | None = None
    context_length: int | None = None
    persist_user_history: bool = True
    plan_session_key: str | None = None


@dataclass
class RunTurnResult:
    message: MessageDict | None
    usage: dict[str, Any]
    raw_plan_text: str | None = None


class ApplicationService:
    """Central turn runner used by CLI/Web adapters.

    Adapters still own transport concerns (terminal input, WebSocket queues,
    active-task locks), while this service owns the common LLM/tool/persist/plan
    flow.
    """

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
        settings = runtime.settings if runtime is not None else None
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
            options.streaming = options.streaming if options.streaming is not None else (settings.streaming if settings else STREAMING)
            options.context_length = options.context_length if options.context_length is not None else (settings.model.context_length if settings else MODEL_CONFIG.get("context_length", 256000))
            options.max_turns = options.max_turns or (settings.runner.max_turns if settings else 0)
        options = options or RunTurnOptions()
        if options.streaming is None:
            options.streaming = STREAMING
        if options.context_length is None:
            options.context_length = MODEL_CONFIG.get("context_length", 256000)
        if messages is None or tools is None or history_db is None or workspace is None or session_id is None:
            raise ValueError("ApplicationService.run_turn requires messages/tools/history_db/workspace/session_id or runtime")
        if options.tool_registry is None:
            raise ValueError("ApplicationService.run_turn requires a session-local tool_registry")
        ctx = options.request_context
        owns_ctx = ctx is None
        if ctx is None:
            ctx = RequestContext(model_config=MODEL_CONFIG, display=options.display)

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

            if compactor:
                usage = get_usage()
                compactor.maybe_compact(messages, usage["prompt_tokens"], llm_chat, ctx, options.context_length)

            return RunTurnResult(message=msg, usage=dict(get_usage()), raw_plan_text=raw_plan_text)
        finally:
            if owns_ctx:
                ctx.close()
