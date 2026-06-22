"""Application-level chat turn orchestration shared by adapters."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.display_protocol import DisplayProtocol
from ..core.events import DisplayEvent, DisplayEventType
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
    ToolRegistryProtocol,
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
    usage: UsageDict
    raw_plan_text: str | None = None


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
class ChatErrorResult:
    message: MessageDict | None
    usage: UsageDict
    event: dict[str, Any]
    error_text: str


@dataclass(frozen=True, slots=True)
class TeamInboxInjectionResult:
    injected: bool
    count: int = 0


@dataclass(frozen=True, slots=True)
class TeamFollowupTiming:
    deadline: float
    poll_interval: float


@dataclass(frozen=True, slots=True)
class ChatRuntimeDependencies:
    build_system_prompt: Callable[[str, str, Path | None, str | None], str]
    settings_for_model: Callable[[SettingsSnapshot, str | None], SettingsSnapshot]
    build_runtime: Callable[..., SessionRuntimeContext]
    subagent_loader: Any
    mcp_loader: Any


@dataclass(frozen=True, slots=True)
class ChatRuntimeBundle:
    runtime: SessionRuntimeContext
    settings: SettingsSnapshot | None


@dataclass(frozen=True, slots=True)
class ChatPreparedTurn:
    tools: list[ToolDefinition]
    user_messages: list[MessageDict]
    plan_store: PlanStore | None
    user_text_for_history: Any
    options: RunTurnOptions


@dataclass(frozen=True, slots=True)
class ChatAssistantDispatch:
    message: MessageDict | None
    usage: UsageDict
    display_content: str | None = None


PLAN_DISCUSSION_DISPLAY_CONTENT = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
TEAM_FOLLOWUP_INSTRUCTION = "队友回禀已收到。请先 blackboard_read 获取队友写入黑板的结果，再基于回禀和黑板内容回复用户。"


def build_user_message(user_message: str, images: list[dict[str, Any]] | None = None, *, timestamp: str | None = None) -> MessageDict | None:
    """Build a runtime user message from adapter-neutral text and image payloads."""

    if not user_message and not images:
        return None

    message: MessageDict = {"role": "user", "content": user_message, "timestamp": timestamp or now_ts()}
    if images:
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": user_message}]
        for image in images:
            data_url = image.get("dataUrl", "") if isinstance(image, dict) else ""
            if data_url.startswith("data:"):
                content_blocks.append({"type": "image_url", "image_url": {"url": data_url}})
        message["content"] = content_blocks
    return message


def append_user_message(messages: list[MessageDict], user_message: str, images: list[dict[str, Any]] | None = None, *, timestamp: str | None = None) -> MessageDict | None:
    """Append an adapter-neutral user message if there is visible user input."""

    message = build_user_message(user_message, images, timestamp=timestamp)
    if message is not None:
        messages.append(message)
    return message


def visible_user_messages(messages: list[MessageDict]) -> list[MessageDict]:
    """Return non-internal user messages in runtime order."""

    return [message for message in messages if message.get("role") == "user" and not message.get("_internal")]


def persisted_user_payload(message: MessageDict) -> tuple[Any, str]:
    """Return content and metadata JSON for a user message history row."""

    metadata = {key: value for key, value in message.items() if key not in ("role", "content", "timestamp", "_plan_original_content")}
    content = message.get("_plan_original_content", message.get("content", ""))
    return content, json.dumps(metadata) if metadata else ""


def persist_latest_user_message(history_db: HistoryDBProtocol, *, workspace: str, session_id: str, messages: list[MessageDict]) -> list[MessageDict]:
    """Persist the latest visible user message and return all visible user messages."""

    user_msgs = visible_user_messages(messages)
    if user_msgs:
        content, metadata = persisted_user_payload(user_msgs[-1])
        history_db.append(workspace, session_id, "user", content, metadata=metadata)
    return user_msgs


def prepare_plan_turn(messages: list[MessageDict], *, current_plan: MessageDict | None, selected_option_id: str | None) -> None:
    """Wrap the latest user message as a plan-mode instruction."""

    plan_user = next((message for message in reversed(messages) if message.get("role") == "user"), None)
    if plan_user and isinstance(plan_user.get("content"), str):
        plan_user["_plan_original_content"] = plan_user.get("content", "")
        plan_user["content"] = build_plan_user_message(
            plan_user.get("content", ""),
            current_plan=current_plan,
            selected_option_id=selected_option_id,
        )


def prepare_execution_turn(
    messages: list[MessageDict],
    *,
    approved_plan: MessageDict,
    session_key: str,
    display: DisplayProtocol | None = None,
    timestamp: str | None = None,
    plan_service: PlanService | None = None,
) -> None:
    """Seed execution todos and append the internal execution instruction."""

    plan_svc = plan_service or PlanService()
    plan_svc.seed_execution_todos(artifact=approved_plan, session_key=session_key, display=display)
    messages.append({"role": "user", "content": plan_svc.execution_instruction(approved_plan), "timestamp": timestamp or now_ts(), "_internal": True})


def ensure_session_system_prompt(
    messages: list[MessageDict],
    *,
    username: str,
    session_id: str,
    base: Path | None,
    workspace: str | None,
    build_system_prompt: Callable[[str, str, Path | None, str | None], str],
) -> None:
    """Refresh placeholder system prompts before runtime construction."""

    if messages and messages[0]["role"] == "system" and len(messages[0]["content"]) < 50:
        messages[0]["content"] = build_system_prompt(username, session_id, base, workspace)


def build_chat_runtime_bundle(
    deps: ChatRuntimeDependencies,
    *,
    username: str,
    workspace: str | None,
    session_id: str,
    base: Path | None,
    messages: list[MessageDict],
    display: DisplayProtocol | None,
    components: dict[str, Any],
    abort_event: threading.Event | None,
    model_name: str | None,
) -> ChatRuntimeBundle:
    """Build a session runtime from adapter-neutral Web chat components."""

    ensure_session_system_prompt(
        messages,
        username=username,
        session_id=session_id,
        base=base,
        workspace=workspace,
        build_system_prompt=deps.build_system_prompt,
    )
    base_settings = components.get("settings")
    runtime_settings = deps.settings_for_model(base_settings, model_name) if base_settings else None
    cfg = runtime_settings.model.to_dict() if runtime_settings else None
    runtime = deps.build_runtime(
        identity=SessionIdentity(
            username=username or "default",
            workspace=workspace or "default",
            session_id=session_id,
            project_path=components.get("project_path") or "",
        ),
        messages=messages,
        display=display,
        history_db=components.get("history_db"),
        memory_store=components.get("store"),
        skill_loader=components.get("skill_loader"),
        subagent_loader=deps.subagent_loader,
        bus=components.get("bus"),
        team_mgr=components.get("team_mgr"),
        blackboard=components.get("blackboard"),
        abort_event=abort_event,
        model_config=cfg,
        settings=runtime_settings,
        mcp_loader=deps.mcp_loader,
        compactor=components.get("compactor"),
        context_builder=components.get("ctx_builder"),
    )
    return ChatRuntimeBundle(runtime=runtime, settings=runtime_settings)


def default_chat_tools(tool_registry: ToolRegistryProtocol) -> list[ToolDefinition]:
    """Return default Web chat tools without teammate inbox-management tools."""

    return [definition for definition in tool_registry.get_definitions() if definition["function"]["name"] not in ("read_inbox", "list_teammates")]


def select_turn_tools(tools: list[ToolDefinition], *, plan_turn: bool) -> list[ToolDefinition]:
    """Apply plan/execution policy to a turn's tool definitions."""

    policy = ToolPolicy.PLAN_READONLY if plan_turn else ToolPolicy.EXECUTION
    return filter_tools(tools, policy)


def prepare_chat_turn(
    *,
    runtime: SessionRuntimeContext,
    messages: list[MessageDict],
    tools: list[ToolDefinition] | None,
    history_db: HistoryDBProtocol,
    workspace: str,
    session_id: str,
    session_key: str,
    plan_state_store: PlanStateStoreProtocol,
    plan_turn: bool,
    approved_plan: MessageDict | None,
    max_turns: int,
    abort_event: threading.Event | None,
    display: DisplayProtocol | None,
) -> ChatPreparedTurn:
    """Prepare tools, history, plan state and run options for a chat turn."""

    selected_tools = default_chat_tools(runtime.tool_registry) if tools is None else tools
    plan_state = plan_state_store.get_plan_state(session_key)
    selected_tools = select_turn_tools(selected_tools, plan_turn=plan_turn)
    if plan_turn:
        prepare_plan_turn(messages, current_plan=plan_state.current_plan, selected_option_id=plan_state.selected_option_id)
    elif approved_plan:
        prepare_execution_turn(messages, approved_plan=approved_plan, session_key=session_key, display=display)

    user_messages = persist_latest_user_message(history_db, workspace=workspace, session_id=session_id, messages=messages)
    return ChatPreparedTurn(
        tools=selected_tools,
        user_messages=user_messages,
        plan_store=PlanStore(history_db, workspace, session_id) if (plan_turn or approved_plan) else None,
        user_text_for_history=(user_messages[-1].get("_plan_original_content", user_messages[-1].get("content", "")) if user_messages else None),
        options=RunTurnOptions(
            streaming=None,
            abort_event=abort_event,
            max_turns=max_turns,
            plan_turn=plan_turn,
            approved_plan=approved_plan,
            context_length=None,
            persist_user_history=False,
            plan_session_key=session_key,
        ),
    )


def valid_assistant_result(message: MessageDict | None) -> bool:
    """Return whether an assistant message has content or tool calls."""

    return bool(message and (message.get("content") or message.get("tool_calls")))


def finalize_chat_assistant_response(
    *,
    messages: list[MessageDict],
    result: RunTurnResult,
    plan_turn: bool,
) -> ChatAssistantDispatch:
    """Persist/convert a successful assistant result and return display metadata."""

    message = result.message
    display_content = None
    if message and message.get("content") and message["content"].strip():
        if plan_turn:
            display_content = apply_plan_discussion_response(messages, message, result.raw_plan_text)
        else:
            append_chat_assistant_message(messages, message)
    return ChatAssistantDispatch(message=message, usage=result.usage, display_content=display_content)


def fallback_error_text(message: MessageDict | None) -> str:
    """Return the assistant-visible fallback error text for an invalid model response."""

    if message is None:
        return "⚠ LLM 未返回有效回复（可能因限流或错误）"
    if message.get("interrupted"):
        return "⏸ 生成已中断"
    if message.get("error"):
        return f"⚠ {message.get('error')}"
    return "⚠ LLM 未返回有效回复（可能因限流或错误）"


def chat_error_context(*, session_id: str, workspace: str | None, messages: list[MessageDict]) -> dict[str, Any]:
    """Build diagnostic context for a failed chat turn."""

    context: dict[str, Any] = {
        "session_id": session_id,
        "workspace": workspace,
        "message_count": len(messages),
        "last_user_message": None,
        "last_tool_calls": [],
    }
    for message in reversed(messages[-5:]):
        if message.get("role") == "user":
            context["last_user_message"] = str(message.get("content", ""))[:200]
            break
    for message in reversed(messages[-10:]):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            context["last_tool_calls"] = [
                {"name": tool_call.get("function", {}).get("name"), "id": tool_call.get("id")}
                for tool_call in message["tool_calls"][:3]
            ]
            break
    return context


def handle_invalid_chat_result(
    *,
    message: MessageDict | None,
    messages: list[MessageDict],
    history_db: HistoryDBProtocol,
    workspace: str,
    history_session_id: str,
    event_session_id: str,
    usage: UsageDict,
    timestamp: str | None = None,
) -> ChatErrorResult:
    """Append/persist a fallback assistant error and build the complete event."""

    err_text = fallback_error_text(message)
    messages.append({"role": "assistant", "content": err_text, "timestamp": timestamp or now_ts()})
    history_db.append(workspace, history_session_id, "assistant", err_text)
    event = DisplayEvent(
        DisplayEventType.COMPLETE,
        {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "error": err_text,
            "error_context": chat_error_context(session_id=event_session_id, workspace=workspace, messages=messages),
            "session_id": event_session_id,
        },
    ).to_wire()
    return ChatErrorResult(message=message, usage=usage, event=event, error_text=err_text)


def apply_plan_discussion_response(messages: list[MessageDict], message: MessageDict, raw_plan_text: str | None) -> str:
    """Replace raw plan artifact text with display-friendly plan discussion content."""

    message["content"] = PLAN_DISCUSSION_DISPLAY_CONTENT
    message["kind"] = "plan_discussion"
    for existing in reversed(messages):
        if existing.get("role") == "assistant" and existing.get("content") == raw_plan_text:
            existing["content"] = PLAN_DISCUSSION_DISPLAY_CONTENT
            existing["kind"] = "plan_discussion"
            break
    return PLAN_DISCUSSION_DISPLAY_CONTENT


def append_chat_assistant_message(messages: list[MessageDict], message: MessageDict, *, timestamp: str | None = None) -> MessageDict | None:
    """Append a normal assistant response unless the runner already persisted it."""

    content = message.get("content")
    if not content or not str(content).strip():
        return None
    if any(existing.get("role") == "assistant" and existing.get("content") == content for existing in messages[-3:]):
        return None

    assistant_message = {
        "role": "assistant",
        "content": content,
        "thinking": message.get("thinking"),
        "timestamp": timestamp or now_ts(),
        "kind": "chat",
    }
    messages.append(assistant_message)
    return assistant_message


def should_poll_team_followup(bus: MessageBusProtocol | None, team_mgr: Any | None, message: MessageDict | None) -> bool:
    """Return whether a turn should poll teammate inbox replies."""

    return bool(bus and team_mgr and message is not None and not message.get("error") and message.get("tool_calls"))


def team_followup_timing(timeout_settings: Any | None, *, now: float) -> TeamFollowupTiming:
    """Return follow-up polling deadline and interval from runtime timeout settings."""

    lead_wait = timeout_settings.lead_wait if timeout_settings else 1800
    poll_interval = timeout_settings.lead_poll_interval if timeout_settings else 2
    return TeamFollowupTiming(deadline=now + lead_wait, poll_interval=poll_interval)


def inject_team_inbox_messages(messages: list[MessageDict], inbox_messages: list[dict[str, Any]], *, label: str = "兜底", timestamp: str | None = None) -> TeamInboxInjectionResult:
    """Inject teammate inbox replies into the conversation for a follow-up turn."""

    from ..team.loop import format_inbox_messages

    inbox_text = format_inbox_messages(inbox_messages)
    if not inbox_text:
        return TeamInboxInjectionResult(injected=False)

    ts = timestamp or now_ts()
    messages.append({"role": "user", "content": inbox_text, "timestamp": ts})
    messages.append({"role": "user", "content": TEAM_FOLLOWUP_INSTRUCTION, "timestamp": ts})
    return TeamInboxInjectionResult(injected=True, count=len(inbox_messages))


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
