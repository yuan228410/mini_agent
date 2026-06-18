"""Structured models for team/workflow state boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InboxMessageType(StrEnum):
    MESSAGE = "message"
    BROADCAST = "broadcast"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    TASK_HANDOFF = "task_handoff"


def preview_text(text: str, limit: int = 100) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


@dataclass(slots=True)
class InboxMessage:
    msg_type: InboxMessageType
    sender: str
    content: str
    timestamp: float
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InboxMessage":
        known = {"type", "from", "content", "timestamp"}
        msg_type = data.get("type") or InboxMessageType.MESSAGE.value
        try:
            msg_type = InboxMessageType(msg_type)
        except ValueError:
            msg_type = InboxMessageType.MESSAGE
        return cls(
            msg_type=msg_type,
            sender=str(data.get("from", "")),
            content=str(data.get("content", "")),
            timestamp=float(data.get("timestamp") or 0.0),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "type": self.msg_type.value,
            "from": self.sender,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        data.update(self.extra)
        return data


@dataclass(slots=True)
class BlackboardEntry:
    value: str
    author: str = ""
    ts: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | str) -> "BlackboardEntry":
        if isinstance(data, dict):
            return cls(
                value=str(data.get("value", "")),
                author=str(data.get("author", "")),
                ts=float(data.get("ts") or 0.0),
            )
        return cls(value=str(data))

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "author": self.author, "ts": self.ts}


@dataclass(slots=True)
class WorkflowTaskInfo:
    id: str
    agent: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "prompt": self.prompt,
            "depends_on": list(self.depends_on),
        }


@dataclass(slots=True)
class WorkflowTaskStart:
    id: str
    agent: str
    prompt: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "agent": self.agent, "prompt": self.prompt}


@dataclass(slots=True)
class WorkflowTaskEnd:
    id: str
    status: str
    result_preview: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "status": self.status}
        if self.result_preview is not None:
            payload["result_preview"] = self.result_preview
        if self.error is not None:
            payload["error"] = self.error
        return payload
