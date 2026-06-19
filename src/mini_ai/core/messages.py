"""Structured chat-message models and legacy dict converters.

The runtime still carries ``list[dict]`` for compatibility with persistence and
provider adapters, but new boundaries should normalize through these DTOs so the
core schema is explicit and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .runtime_types import MessageDict
from .tool_models import ToolCall


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


_INTERNAL_FIELDS = frozenset(("_pruned", "_prune_level", "_is_summary", "thinking"))


@dataclass(slots=True)
class ChatMessage:
    role: MessageRole
    content: Any = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    timestamp: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: MessageDict) -> "ChatMessage":
        known = {"role", "content", "tool_calls", "tool_call_id", "name", "timestamp"}
        role = MessageRole(data.get("role") or MessageRole.USER)
        return cls(
            role=role,
            content=data.get("content"),
            tool_calls=[ToolCall.from_dict(tc) for tc in data.get("tool_calls") or [] if isinstance(tc, dict)],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            timestamp=data.get("timestamp"),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self, *, include_internal: bool = True, include_tool_results: bool = True) -> MessageDict:
        msg: MessageDict = {"role": self.role.value}
        if self.content is not None or self.role is not MessageRole.ASSISTANT:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_dict(include_result=include_tool_results) for tc in self.tool_calls]
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        if self.timestamp is not None:
            msg["timestamp"] = self.timestamp
        for key, value in self.extra.items():
            if include_internal or key not in _INTERNAL_FIELDS:
                msg[key] = value
        return msg


def normalize_messages(messages: list[MessageDict]) -> list[ChatMessage]:
    return [ChatMessage.from_dict(m) for m in messages]


def to_provider_messages(messages: list[MessageDict]) -> list[MessageDict]:
    """Convert runtime messages to provider-safe wire dicts.

    This removes internal runtime metadata and strips cached tool results from
    assistant tool calls. It also drops orphan tool messages that would violate
    provider message ordering constraints.
    """

    converted = [
        ChatMessage.from_dict(m).to_dict(include_internal=False, include_tool_results=False)
        for m in messages
    ]

    has_assistant_tool_calls = any(
        m.get("role") == MessageRole.ASSISTANT.value and m.get("tool_calls") for m in converted
    )
    if not has_assistant_tool_calls:
        return [m for m in converted if m.get("role") != MessageRole.TOOL.value]

    valid_tool_ids: set[str] = set()
    for m in converted:
        if m.get("role") == MessageRole.ASSISTANT.value and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if isinstance(tc, dict) and tc.get("id"):
                    valid_tool_ids.add(tc["id"])

    return [
        m for m in converted
        if m.get("role") != MessageRole.TOOL.value or m.get("tool_call_id") in valid_tool_ids
    ]
