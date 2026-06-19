"""Structured tool-call models used at core boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .runtime_types import MessageDict, ToolFunctionPayload, ToolWirePayload


@dataclass(slots=True)
class ToolFunctionCall:
    name: str
    arguments: str = ""

    @classmethod
    def from_dict(cls, data: ToolFunctionPayload) -> "ToolFunctionCall":
        return cls(name=str(data.get("name") or ""), arguments=str(data.get("arguments") or ""))

    def to_dict(self) -> ToolFunctionPayload:
        return {"name": self.name, "arguments": self.arguments}


@dataclass(slots=True)
class ToolCall:
    id: str
    function: ToolFunctionCall
    type: str = "function"
    result_preview: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: ToolWirePayload) -> "ToolCall":
        known = {"id", "type", "function", "_result"}
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or "function"),
            function=ToolFunctionCall.from_dict(data.get("function") or {}),
            result_preview=data.get("_result"),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self, *, include_result: bool = True) -> ToolWirePayload:
        data: ToolWirePayload = {"id": self.id, "type": self.type, "function": self.function.to_dict()}
        data.update(self.extra)
        if include_result and self.result_preview is not None:
            data["_result"] = self.result_preview
        return data


@dataclass(slots=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str

    @classmethod
    def from_message(cls, message: MessageDict) -> "ToolResult":
        return cls(
            tool_call_id=str(message.get("tool_call_id") or ""),
            name=str(message.get("name") or ""),
            content=str(message.get("content") or ""),
        )

    def to_message(self, **extra: Any) -> MessageDict:
        msg: MessageDict = {"role": "tool", "tool_call_id": self.tool_call_id, "name": self.name, "content": self.content}
        msg.update(extra)
        return msg
