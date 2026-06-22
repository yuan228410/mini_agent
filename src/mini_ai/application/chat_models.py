"""Shared chat application DTOs."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ..core.display_protocol import DisplayProtocol
from ..core.runtime_context import SessionRuntimeContext
from ..core.runtime_types import MessageDict, RequestContextProtocol, ToolDefinition, ToolRegistryProtocol, UsageDict
from ..plan.store import PlanStore


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
class ChatRuntimeBundle:
    runtime: SessionRuntimeContext
    settings: Any | None


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
