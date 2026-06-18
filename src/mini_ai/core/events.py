"""Structured event names and payload helpers for display/Web adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DisplayEventType(StrEnum):
    CONNECTED = "connected"
    RECONNECTED = "reconnected"
    DISCONNECTED = "disconnected"
    PONG = "pong"

    LLM_ROUND_START = "llm_round_start"
    LLM_ROUND_END = "llm_round_end"
    THINKING_START = "thinking_start"
    THINKING = "thinking"
    THINKING_END = "thinking_end"
    TEXT = "text"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    TODOS = "todos"
    DONE = "done"
    COMPLETE = "complete"
    PLAN_EVENT = "plan_event"
    MODE_CHANGE = "mode_change"
    ABORTED = "aborted"
    TEAMMATE_STATUS = "teammate_status"
    BLACKBOARD_UPDATE = "blackboard_update"
    INBOX_MESSAGE = "inbox_message"
    INFO = "info"
    ERROR = "error"
    WORKFLOW_START = "workflow_start"
    WORKFLOW_TASK_START = "task_start"
    WORKFLOW_TASK_END = "task_end"
    WORKFLOW_END = "workflow_end"
    AGENT_START = "agent_start"


TERMINAL_EVENT_TYPES = {
    DisplayEventType.ERROR,
    DisplayEventType.COMPLETE,
    DisplayEventType.DONE,
    DisplayEventType.ABORTED,
}


@dataclass(slots=True)
class DisplayEvent:
    event: DisplayEventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {"event": self.event.value, "data": self.data}


def event_payload(event: DisplayEventType | str, **data: Any) -> DisplayEvent:
    return DisplayEvent(DisplayEventType(event), data)


def llm_round_start(model: str = "") -> DisplayEvent:
    return event_payload(DisplayEventType.LLM_ROUND_START, model=model)


def llm_round_end(*, prompt_tokens: int = 0, completion_tokens: int = 0, elapsed: float = 0.0, model: str = "") -> DisplayEvent:
    return event_payload(
        DisplayEventType.LLM_ROUND_END,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed=round(elapsed, 2),
        model=model,
    )


def text(content: str) -> DisplayEvent:
    return event_payload(DisplayEventType.TEXT, content=content)


def tool_start(name: str, args: str, tool_call_id: str = "") -> DisplayEvent:
    return event_payload(DisplayEventType.TOOL_START, name=name, args=args, tool_call_id=tool_call_id)


def tool_result(name: str, result: str, elapsed: float, tool_call_id: str = "") -> DisplayEvent:
    return event_payload(DisplayEventType.TOOL_RESULT, name=name, result=result, elapsed=round(elapsed, 1), tool_call_id=tool_call_id)


def todos(content: str) -> DisplayEvent:
    return event_payload(DisplayEventType.TODOS, content=content)


def plan_event(kind: str, **data: Any) -> DisplayEvent:
    payload = {"kind": kind}
    payload.update(data)
    return DisplayEvent(DisplayEventType.PLAN_EVENT, payload)


def agent_start(agent_type: str, *, task: str = "", role: str = "", max_turns: int | None = None) -> DisplayEvent:
    payload: dict[str, Any] = {"agent_type": agent_type}
    if task:
        payload["task"] = task
    if role:
        payload["role"] = role
    if max_turns is not None:
        payload["max_turns"] = max_turns
    return DisplayEvent(DisplayEventType.AGENT_START, payload)


def workflow_start(tasks: list[dict[str, Any]], total: int) -> DisplayEvent:
    return event_payload(DisplayEventType.WORKFLOW_START, tasks=tasks, total=total)


def workflow_task_start(task_id: str, agent: str, prompt: str) -> DisplayEvent:
    return event_payload(DisplayEventType.WORKFLOW_TASK_START, id=task_id, agent=agent, prompt=prompt)


def workflow_task_end(task_id: str, status: str, *, result_preview: str | None = None, error: str | None = None) -> DisplayEvent:
    payload: dict[str, Any] = {"id": task_id, "status": status}
    if result_preview is not None:
        payload["result_preview"] = result_preview
    if error is not None:
        payload["error"] = error
    return DisplayEvent(DisplayEventType.WORKFLOW_TASK_END, payload)


def workflow_end(elapsed: float, completed: int, failed: int, total: int) -> DisplayEvent:
    return event_payload(DisplayEventType.WORKFLOW_END, elapsed=elapsed, completed=completed, failed=failed, total=total)
