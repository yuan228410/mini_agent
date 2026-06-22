"""Application-level chat turn orchestration shared by adapters."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..core.display_protocol import DisplayProtocol
from ..core.persister import HistoryPersister
from ..core.runtime_context import SessionRuntimeContext
from ..core.runtime_factory import build_request_context
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
from ..plan.service import PlanService
from ..plan.store import PlanStore
from ..plan.tool_policy import ToolPolicy, filter_tools
from ..runner import run_tool_loop
from ..tools import inject_todos as _inject_todos
from ..utils import now_ts


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
class ChatExportResult:
    content: str
    filename: str


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


def chat_history(deps: ChatRestDependencies, *, session_id: str = "", username: str, workspace: str = "") -> dict[str, Any]:
    """Return display-ready chat history for a Web session."""

    if not username:
        return {"session_id": session_id, "history": []}
    if not session_id:
        return {"session_id": "", "history": []}
    try:
        base = deps.resolve_base(username, workspace or None)
    except Exception:
        return {"session_id": session_id, "history": []}

    key = deps.cache_key(username, workspace or None, session_id)
    mem_msgs = deps.session_manager.get_messages(key)

    if mem_msgs:
        messages = [m for m in mem_msgs if m["role"] not in ("system", "tool")]
        comp = deps.get_or_create_components(username, session_id, base, workspace or None)
        current_plan = comp["history_db"].get_current_plan(workspace or "default", session_id)
    else:
        comp = deps.get_or_create_components(username, session_id, base, workspace or None)
        messages = comp["history_db"].load_session_for_display(workspace or "default", session_id) or []
        current_plan = comp["history_db"].get_current_plan(workspace or "default", session_id)

    history: list[dict[str, Any]] = []
    for message in messages:
        entry: dict[str, Any] = {"role": message["role"]}
        content = message.get("content")

        if isinstance(content, list):
            text_parts = []
            images: list[dict[str, Any]] = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        img_url = part.get("image_url", {}).get("url", "")
                        if img_url:
                            images.append({"dataUrl": img_url, "name": "", "size": 0})
            entry["content"] = "\n".join(text_parts)
            if images:
                entry["images"] = images
        elif content:
            entry["content"] = content

        for key_name in ("timestamp", "thinking", "tool_calls", "kind", "plan"):
            if message.get(key_name):
                entry[key_name] = message[key_name]
        history.append(entry)

    return {"session_id": session_id, "history": history, "current_plan": current_plan}


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


def export_chat(
    deps: ChatRestDependencies,
    *,
    session_id: str = "",
    username: str,
    workspace: str = "",
    limit: int = 0,
    include_thinking: bool = False,
    include_tools: bool = False,
) -> ChatExportResult | dict[str, Any]:
    """Build a markdown export for a chat session."""

    if not username:
        return {"error": "缺少 username", "status_code": 400}
    if not session_id:
        return {"error": "缺少 session_id", "status_code": 400}
    try:
        base = deps.resolve_base(username, workspace or None)
    except Exception as exc:
        return {"error": f"工作空间错误: {exc}", "status_code": 400}

    comp = deps.get_or_create_components(username, session_id, base, workspace or None)
    messages = comp["history_db"].load_session(workspace or "default", session_id, limit=limit) or []
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在或无消息", "status_code": 404}

    session_name = deps.load_session_name(base, session_id)
    if not session_name:
        for message in messages:
            if message.get("role") == "user" and message.get("content"):
                session_name = message["content"][:50]
                break
    if not session_name:
        session_name = session_id

    lines = [f"# {session_name}\n"]
    for message in messages:
        role = message.get("role", "")
        content = message.get("content") or ""
        ts = message.get("timestamp", "")
        if role in ("system", "tool"):
            continue
        if role == "user":
            label = "**🧑 用户**"
            if ts:
                label += f"  `{ts}`"
            lines.append(f"\n{label}\n\n{content}\n")
        elif role == "assistant":
            thinking = message.get("thinking")
            tool_calls = message.get("tool_calls")
            has_thinking = include_thinking and thinking
            has_tools = include_tools and tool_calls
            if not content and not has_thinking and not has_tools:
                continue
            label = "**🤖 助手**"
            if ts:
                label += f"  `{ts}`"
            lines.append(f"\n{label}\n")
            if has_thinking:
                thinking_text = thinking if isinstance(thinking, str) else str(thinking)
                lines.append(f"\n<details>\n<summary>💭 思考过程</summary>\n\n{thinking_text}\n\n</details>\n")
            if has_tools:
                for tool_call in tool_calls:
                    fn = tool_call.get("function", {})
                    name = fn.get("name", "?")
                    args = str(fn.get("arguments", ""))
                    result = tool_call.get("_result", "")
                    lines.append(f"\n> 🔧 **{name}**({args[:200]})\n")
                    if result:
                        lines.append(f"> 结果: {result[:500]}\n")
            if content:
                lines.append(f"\n{content}\n")

    safe_name = session_name.replace("/", "-").replace(" ", "-")[:60]
    return ChatExportResult(content="\n".join(lines), filename=safe_name)


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
