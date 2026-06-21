"""Standard tool execution result DTOs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Normalized result for one tool invocation.

    Tool implementations may still return plain strings.  The registry normalizes
    both forms so scheduler/policy layers can consume metadata without parsing
    user-visible text.
    """

    content: str = ""
    ok: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, content: object = "", **metadata: Any) -> "ToolExecutionResult":
        return cls(content="" if content is None else str(content), ok=True, metadata=metadata)

    @classmethod
    def error(cls, content: object = "", **metadata: Any) -> "ToolExecutionResult":
        return cls(content="" if content is None else str(content), ok=False, metadata=metadata)

    @classmethod
    def policy_denied(cls, content: object, **metadata: Any) -> "ToolExecutionResult":
        return cls.error(content, policy_denied=True, **metadata)

    @classmethod
    def from_value(cls, value: object) -> "ToolExecutionResult":
        if isinstance(value, cls):
            return value
        return cls.success(value)
