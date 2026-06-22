"""Chat turn preparation helpers."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from ..core.display_protocol import DisplayProtocol
from ..core.runtime_context import SessionIdentity, SessionRuntimeContext
from ..core.runtime_types import HistoryDBProtocol, MessageDict, PlanStateStoreProtocol, ToolDefinition, ToolRegistryProtocol
from ..core.settings import SettingsSnapshot
from ..plan.prompts import build_plan_user_message
from ..plan.service import PlanService
from ..plan.store import PlanStore
from ..plan.tool_policy import ToolPolicy, filter_tools
from ..utils import now_ts
from .chat_models import ChatPreparedTurn, ChatRuntimeBundle, RunTurnOptions


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
    deps,
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
    base_settings: SettingsSnapshot | None = components.get("settings")
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
