"""WebSocket chat command dispatch helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..application import chat_compact_service, plan_command_service
from ..core.events import DisplayEvent, DisplayEventType
from ..core.runtime_types import DisplayWireEvent, PlanArtifactDict
from .route_types import ImageUpload

SendChatEvent = Callable[[DisplayWireEvent | DisplayEvent], Awaitable[None]]
LaunchChatTask = Callable[[str, str, str, str | None, list[ImageUpload] | None, bool, PlanArtifactDict | None, str | None], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChatCommandDependencies:
    session_manager: Any
    cache_key: Callable[[str, str | None, str], str]
    plan_dependencies: plan_command_service.PlanCommandDependencies
    compact_dependencies: chat_compact_service.ChatCompactDependencies


@dataclass(frozen=True, slots=True)
class ChatCommandState:
    username: str | None


def ws_event(event: DisplayEventType | str, **data) -> DisplayWireEvent:
    """Build a WebSocket wire event."""

    event_type = event if isinstance(event, DisplayEventType) else DisplayEventType(event)
    return DisplayEvent(event_type, data).to_wire()


def error_event(error: str, session_id: str | None = None) -> DisplayWireEvent:
    """Build a WebSocket error event."""

    payload = {"error": error}
    if session_id:
        payload["session_id"] = session_id
    return DisplayEvent(DisplayEventType.ERROR, payload).to_wire()


async def send_plan_command_result(result: plan_command_service.PlanCommandResult, *, send: SendChatEvent, launch_chat_task: LaunchChatTask) -> None:
    """Send plan command events and launch the requested chat task if present."""

    for event in result.events:
        await send(event)
    if result.run:
        run = result.run
        await launch_chat_task(run.sid, run.username, run.user_message, run.workspace, run.images, run.plan_turn, run.approved_plan, result.session_key)


async def dispatch_chat_ws_message(
    raw: str,
    *,
    username: str | None,
    deps: ChatCommandDependencies,
    send: SendChatEvent,
    launch_chat_task: LaunchChatTask,
) -> ChatCommandState:
    """Dispatch one raw WebSocket message and return updated connection state."""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await send(error_event("无效 JSON"))
        return ChatCommandState(username=username)

    msg_type = data.get("type")

    if msg_type == "login":
        next_username = data.get("username", "").strip()
        return ChatCommandState(username=next_username or username)

    if msg_type == "ping":
        await send(ws_event(DisplayEventType.PONG))
        return ChatCommandState(username=username)

    if not username:
        await send(error_event("请先发送 login 消息"))
        return ChatCommandState(username=username)

    if msg_type == "abort":
        abort_sid = data.get("session_id")
        abort_workspace = data.get("workspace")
        if abort_sid:
            evt = deps.session_manager.get_abort_event(deps.cache_key(username, abort_workspace, abort_sid))
            if evt:
                evt.set()
        return ChatCommandState(username=username)

    session_id = data.get("session_id")
    workspace = data.get("workspace")

    if msg_type and msg_type.startswith("plan."):
        result = plan_command_service.handle_plan_command(
            deps.plan_dependencies,
            username=username,
            session_id=session_id,
            workspace=workspace,
            msg_type=msg_type,
            payload=data,
        )
        await send_plan_command_result(result, send=send, launch_chat_task=launch_chat_task)
        return ChatCommandState(username=username)

    if msg_type != "chat":
        return ChatCommandState(username=username)

    user_message = data.get("message", "").strip()
    images = data.get("images")

    if user_message == "/compact":
        result = chat_compact_service.compact_chat(deps.compact_dependencies, username=username, session_id=session_id, workspace=workspace)
        await send(result.event)
        return ChatCommandState(username=username)

    if user_message == "/act":
        result = plan_command_service.approve_current_plan(deps.plan_dependencies, username=username, session_id=session_id, workspace=workspace)
        await send_plan_command_result(result, send=send, launch_chat_task=launch_chat_task)
        return ChatCommandState(username=username)

    if not session_id:
        await send(error_event("请先选择会话"))
        return ChatCommandState(username=username)

    await launch_chat_task(session_id, username, user_message, workspace, images, False, None, None)
    return ChatCommandState(username=username)
