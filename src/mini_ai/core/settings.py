"""Typed immutable settings snapshots for runtime boundaries.

The config module still owns loading YAML and compatibility globals.  Runtime code
should capture the pieces it needs into these DTOs so a session observes a stable,
explicit settings shape instead of reaching through loosely-typed global dicts.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelSettings:
    api_url: str
    api_key: str
    model: str
    api_mode: str = "openai"
    context_length: int = 256000
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    thinking: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ModelSettings":
        raw = copy.deepcopy(data or {})
        known = {
            "api_url", "api_key", "model", "api_mode", "context_length",
            "temperature", "max_tokens", "top_p", "reasoning_effort",
            "headers", "thinking",
        }
        return cls(
            api_url=str(raw.get("api_url") or ""),
            api_key=str(raw.get("api_key") or ""),
            model=str(raw.get("model") or ""),
            api_mode=str(raw.get("api_mode") or "openai"),
            context_length=int(raw.get("context_length") or 256000),
            temperature=raw.get("temperature"),
            max_tokens=raw.get("max_tokens"),
            top_p=raw.get("top_p"),
            reasoning_effort=raw.get("reasoning_effort"),
            headers=copy.deepcopy(raw.get("headers") or {}),
            thinking=copy.deepcopy(raw.get("thinking") or {}),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "api_url": self.api_url,
            "api_key": self.api_key,
            "model": self.model,
            "api_mode": self.api_mode,
            "context_length": self.context_length,
        }
        optional = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "reasoning_effort": self.reasoning_effort,
        }
        data.update({k: v for k, v in optional.items() if v is not None})
        if self.headers:
            data["headers"] = copy.deepcopy(self.headers)
        if self.thinking:
            data["thinking"] = copy.deepcopy(self.thinking)
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class TimeoutSettings:
    llm: float = 120
    llm_connect: float = 30
    llm_retries: int = 3
    llm_retry_delay: float = 2
    teammate_recv: float = 5
    lead_wait: float = 1800
    lead_poll_interval: float = 2
    web_fetch: float = 20
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TimeoutSettings":
        raw = copy.deepcopy(data or {})
        known = {
            "llm", "llm_connect", "llm_retries", "llm_retry_delay",
            "teammate_recv", "lead_wait", "lead_poll_interval", "web_fetch",
        }
        return cls(
            llm=raw.get("llm", 120),
            llm_connect=raw.get("llm_connect", 30),
            llm_retries=int(raw.get("llm_retries", 3)),
            llm_retry_delay=raw.get("llm_retry_delay", 2),
            teammate_recv=raw.get("teammate_recv", 5),
            lead_wait=raw.get("lead_wait", 1800),
            lead_poll_interval=raw.get("lead_poll_interval", 2),
            web_fetch=raw.get("web_fetch", 20),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "llm": self.llm,
            "llm_connect": self.llm_connect,
            "llm_retries": self.llm_retries,
            "llm_retry_delay": self.llm_retry_delay,
            "teammate_recv": self.teammate_recv,
            "lead_wait": self.lead_wait,
            "lead_poll_interval": self.lead_poll_interval,
            "web_fetch": self.web_fetch,
        }
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    context_usage_limit: float = 0.88
    max_turns: int = 20
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RunnerSettings":
        raw = copy.deepcopy(data or {})
        return cls(
            context_usage_limit=float(raw.get("context_usage_limit", 0.88)),
            max_turns=int(raw.get("max_turns", 20)),
            extra={k: v for k, v in raw.items() if k not in {"context_usage_limit", "max_turns"}},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"context_usage_limit": self.context_usage_limit, "max_turns": self.max_turns}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class DisplaySettings:
    thinking_mode: str = "collapsed"
    tool_detail: str = "summary"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DisplaySettings":
        raw = copy.deepcopy(data or {})
        return cls(
            thinking_mode=str(raw.get("thinking_mode") or "collapsed"),
            tool_detail=str(raw.get("tool_detail") or "summary"),
            extra={k: v for k, v in raw.items() if k not in {"thinking_mode", "tool_detail"}},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"thinking_mode": self.thinking_mode, "tool_detail": self.tool_detail}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class ToolSettings:
    max_result_chars: int = 8000
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ToolSettings":
        raw = copy.deepcopy(data or {})
        return cls(
            max_result_chars=int(raw.get("max_result_chars", 8000)),
            extra={k: v for k, v in raw.items() if k != "max_result_chars"},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"max_result_chars": self.max_result_chars}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class DatabaseHistorySettings:
    async_write: bool | None = None
    batch_size: int = 50
    batch_timeout: float = 0.1
    queue_size: int = 10000
    retry_count: int = 3
    submit_timeout: float = 1.0
    on_full: str = "block"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DatabaseHistorySettings":
        raw = copy.deepcopy(data or {})
        known = {
            "async_write", "batch_size", "batch_timeout", "queue_size",
            "retry_count", "submit_timeout", "on_full",
        }
        return cls(
            async_write=raw.get("async_write"),
            batch_size=int(raw.get("batch_size", 50)),
            batch_timeout=float(raw.get("batch_timeout", 0.1)),
            queue_size=int(raw.get("queue_size", 10000)),
            retry_count=int(raw.get("retry_count", 3)),
            submit_timeout=float(raw.get("submit_timeout", 1.0)),
            on_full=str(raw.get("on_full") or "block"),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "async_write": self.async_write,
            "batch_size": self.batch_size,
            "batch_timeout": self.batch_timeout,
            "queue_size": self.queue_size,
            "retry_count": self.retry_count,
            "submit_timeout": self.submit_timeout,
            "on_full": self.on_full,
        }
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    history: DatabaseHistorySettings = field(default_factory=DatabaseHistorySettings)
    memory: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DatabaseSettings":
        raw = copy.deepcopy(data or {})
        return cls(
            history=DatabaseHistorySettings.from_dict(raw.get("history") or {}),
            memory=copy.deepcopy(raw.get("memory") or {"cache_size": 10000}),
            extra={k: v for k, v in raw.items() if k not in {"history", "memory"}},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"history": self.history.to_dict(), "memory": copy.deepcopy(self.memory)}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    model: ModelSettings
    timeouts: TimeoutSettings = field(default_factory=TimeoutSettings)
    runner: RunnerSettings = field(default_factory=RunnerSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    tool: ToolSettings = field(default_factory=ToolSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    streaming: bool = True

    @classmethod
    def from_config_dicts(
        cls,
        *,
        model_config: dict[str, Any] | None,
        timeouts: dict[str, Any] | None = None,
        runner: dict[str, Any] | None = None,
        display: dict[str, Any] | None = None,
        tool: dict[str, Any] | None = None,
        database: dict[str, Any] | None = None,
        streaming: bool = True,
    ) -> "SettingsSnapshot":
        return cls(
            model=ModelSettings.from_dict(model_config),
            timeouts=TimeoutSettings.from_dict(timeouts),
            runner=RunnerSettings.from_dict(runner),
            display=DisplaySettings.from_dict(display),
            tool=ToolSettings.from_dict(tool),
            database=DatabaseSettings.from_dict(database),
            streaming=bool(streaming),
        )
