"""Typed immutable settings snapshots for runtime boundaries.

The config module still owns loading YAML and compatibility globals.  Runtime code
should capture the pieces it needs into these DTOs so a session observes a stable,
explicit settings shape instead of reaching through loosely-typed global dicts.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace

from .runtime_types import (
    ConfigDict,
    DatabaseConfigDict,
    DatabaseHistoryConfigDict,
    DisplayConfigDict,
    ImageConfigDict,
    McpConfigDict,
    ModelConfigDict,
    RunnerConfigDict,
    CompactorConfigDict,
    TeamConfigSettingsDict,
    TimeoutConfigDict,
    ToolConfigDict,
    WebConfigDict,
    WorkflowConfigDict,
)


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
    headers: ConfigDict = field(default_factory=dict)
    thinking: ConfigDict = field(default_factory=dict)
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: ModelConfigDict | None) -> "ModelSettings":
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

    def to_dict(self) -> ModelConfigDict:
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
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: TimeoutConfigDict | None) -> "TimeoutSettings":
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

    def to_dict(self) -> TimeoutConfigDict:
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
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: RunnerConfigDict | None) -> "RunnerSettings":
        raw = copy.deepcopy(data or {})
        return cls(
            context_usage_limit=float(raw.get("context_usage_limit", 0.88)),
            max_turns=int(raw.get("max_turns", 20)),
            extra={k: v for k, v in raw.items() if k not in {"context_usage_limit", "max_turns"}},
        )

    def to_dict(self) -> RunnerConfigDict:
        data = {"context_usage_limit": self.context_usage_limit, "max_turns": self.max_turns}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class CompactorSettings:
    keep_recent: int = 50
    context_usage_threshold: float = 0.8
    keep_budget_ratio: float = 0.2
    early_compact_ratio: float = 0.85
    max_cached_summaries: int = 200
    max_summary_sections: int = 50
    context_limit: int = 50
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: CompactorConfigDict | None) -> "CompactorSettings":
        raw = copy.deepcopy(data or {})
        known = {
            "keep_recent", "context_usage_threshold", "keep_budget_ratio",
            "early_compact_ratio", "max_cached_summaries", "max_summary_sections",
            "context_limit",
        }
        return cls(
            keep_recent=int(raw.get("keep_recent", 50)),
            context_usage_threshold=float(raw.get("context_usage_threshold", 0.8)),
            keep_budget_ratio=float(raw.get("keep_budget_ratio", 0.2)),
            early_compact_ratio=float(raw.get("early_compact_ratio", 0.85)),
            max_cached_summaries=int(raw.get("max_cached_summaries", 200)),
            max_summary_sections=int(raw.get("max_summary_sections", 50)),
            context_limit=int(raw.get("context_limit", 50)),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> CompactorConfigDict:
        data = {
            "keep_recent": self.keep_recent,
            "context_usage_threshold": self.context_usage_threshold,
            "keep_budget_ratio": self.keep_budget_ratio,
            "early_compact_ratio": self.early_compact_ratio,
            "max_cached_summaries": self.max_cached_summaries,
            "max_summary_sections": self.max_summary_sections,
            "context_limit": self.context_limit,
        }
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class DisplaySettings:
    thinking_mode: str = "collapsed"
    tool_detail: str = "summary"
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: DisplayConfigDict | None) -> "DisplaySettings":
        raw = copy.deepcopy(data or {})
        return cls(
            thinking_mode=str(raw.get("thinking_mode") or "collapsed"),
            tool_detail=str(raw.get("tool_detail") or "summary"),
            extra={k: v for k, v in raw.items() if k not in {"thinking_mode", "tool_detail"}},
        )

    def to_dict(self) -> DisplayConfigDict:
        data = {"thinking_mode": self.thinking_mode, "tool_detail": self.tool_detail}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class ToolSettings:
    max_result_chars: int = 8000
    max_parallel_tools: int = 8
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: ToolConfigDict | None) -> "ToolSettings":
        raw = copy.deepcopy(data or {})
        known = {"max_result_chars", "max_parallel_tools"}
        return cls(
            max_result_chars=int(raw.get("max_result_chars", 8000)),
            max_parallel_tools=max(1, int(raw.get("max_parallel_tools", 8))),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> ToolConfigDict:
        data = {"max_result_chars": self.max_result_chars, "max_parallel_tools": self.max_parallel_tools}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class TeamSettings:
    max_teammates: int = 10
    max_turns: int = 20
    idle_timeout: int = 300
    max_history: int = 20
    task_timeout: int = 600
    base_tools: list[str] = field(default_factory=lambda: [
        "run_command", "web_fetch", "load_skill", "read_file", "write_file", "edit_file", "search_files", "list_dir",
    ])
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: TeamConfigSettingsDict | None) -> "TeamSettings":
        raw = copy.deepcopy(data or {})
        known = {"max_teammates", "max_turns", "idle_timeout", "max_history", "task_timeout", "base_tools"}
        return cls(
            max_teammates=int(raw.get("max_teammates", 10)),
            max_turns=int(raw.get("max_turns", 20)),
            idle_timeout=int(raw.get("idle_timeout", 300)),
            max_history=int(raw.get("max_history", 20)),
            task_timeout=int(raw.get("task_timeout", 600)),
            base_tools=[str(item) for item in raw.get("base_tools", cls().base_tools)],
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> TeamConfigSettingsDict:
        data = {
            "max_teammates": self.max_teammates,
            "max_turns": self.max_turns,
            "idle_timeout": self.idle_timeout,
            "max_history": self.max_history,
            "task_timeout": self.task_timeout,
            "base_tools": list(self.base_tools),
        }
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class WorkflowSettings:
    max_concurrency: int = 8
    task_timeout: int = 600
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: WorkflowConfigDict | None) -> "WorkflowSettings":
        raw = copy.deepcopy(data or {})
        return cls(
            max_concurrency=max(1, int(raw.get("max_concurrency", 8))),
            task_timeout=int(raw.get("task_timeout", 600)),
            extra={k: v for k, v in raw.items() if k not in {"max_concurrency", "task_timeout"}},
        )

    def to_dict(self) -> WorkflowConfigDict:
        data = {"max_concurrency": self.max_concurrency, "task_timeout": self.task_timeout}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class WebSettings:
    history_limit: int = 200
    max_turns: int = 10
    stream_chunk_flush_ms: int = 40
    stream_chunk_max_chars: int = 512
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: WebConfigDict | None) -> "WebSettings":
        raw = copy.deepcopy(data or {})
        known = {"history_limit", "max_turns", "stream_chunk_flush_ms", "stream_chunk_max_chars"}
        return cls(
            history_limit=int(raw.get("history_limit", 200)),
            max_turns=max(1, int(raw.get("max_turns", 10))),
            stream_chunk_flush_ms=max(0, int(raw.get("stream_chunk_flush_ms", 40))),
            stream_chunk_max_chars=max(1, int(raw.get("stream_chunk_max_chars", 512))),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> WebConfigDict:
        data = {
            "history_limit": self.history_limit,
            "max_turns": self.max_turns,
            "stream_chunk_flush_ms": self.stream_chunk_flush_ms,
            "stream_chunk_max_chars": self.stream_chunk_max_chars,
        }
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class McpSettings:
    enabled: bool = False
    connect_timeout: float = 10
    execute_timeout: float = 60
    sse_read_timeout: float = 120
    servers: ConfigDict = field(default_factory=dict)
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: McpConfigDict | None) -> "McpSettings":
        raw = copy.deepcopy(data or {})
        known = {"enabled", "connect_timeout", "execute_timeout", "sse_read_timeout", "servers"}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            connect_timeout=float(raw.get("connect_timeout", 10)),
            execute_timeout=float(raw.get("execute_timeout", 60)),
            sse_read_timeout=float(raw.get("sse_read_timeout", 120)),
            servers=copy.deepcopy(raw.get("servers") or {}),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> McpConfigDict:
        data = {
            "enabled": self.enabled,
            "connect_timeout": self.connect_timeout,
            "execute_timeout": self.execute_timeout,
            "sse_read_timeout": self.sse_read_timeout,
            "servers": copy.deepcopy(self.servers),
        }
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class ImageSettings:
    max_size: int = 10 * 1024 * 1024
    compress_threshold: int = 500 * 1024
    compress_max_dimension: int = 800
    compress_quality: int = 85
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: ImageConfigDict | None) -> "ImageSettings":
        raw = copy.deepcopy(data or {})
        known = {"max_size", "compress_threshold", "compress_max_dimension", "compress_quality"}
        return cls(
            max_size=int(raw.get("max_size", 10 * 1024 * 1024)),
            compress_threshold=int(raw.get("compress_threshold", 500 * 1024)),
            compress_max_dimension=int(raw.get("compress_max_dimension", 800)),
            compress_quality=int(raw.get("compress_quality", 85)),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self) -> ImageConfigDict:
        data = {
            "max_size": self.max_size,
            "compress_threshold": self.compress_threshold,
            "compress_max_dimension": self.compress_max_dimension,
            "compress_quality": self.compress_quality,
        }
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
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: DatabaseHistoryConfigDict | None) -> "DatabaseHistorySettings":
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

    def to_dict(self) -> DatabaseHistoryConfigDict:
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
    memory: ConfigDict = field(default_factory=dict)
    extra: ConfigDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: DatabaseConfigDict | None) -> "DatabaseSettings":
        raw = copy.deepcopy(data or {})
        return cls(
            history=DatabaseHistorySettings.from_dict(raw.get("history") or {}),
            memory=copy.deepcopy(raw.get("memory") or {"cache_size": 10000}),
            extra={k: v for k, v in raw.items() if k not in {"history", "memory"}},
        )

    def to_dict(self) -> DatabaseConfigDict:
        data = {"history": self.history.to_dict(), "memory": copy.deepcopy(self.memory)}
        data.update(copy.deepcopy(self.extra))
        return data


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    model: ModelSettings
    timeouts: TimeoutSettings = field(default_factory=TimeoutSettings)
    runner: RunnerSettings = field(default_factory=RunnerSettings)
    compactor: CompactorSettings = field(default_factory=CompactorSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    tool: ToolSettings = field(default_factory=ToolSettings)
    team: TeamSettings = field(default_factory=TeamSettings)
    workflow: WorkflowSettings = field(default_factory=WorkflowSettings)
    web: WebSettings = field(default_factory=WebSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    image: ImageSettings = field(default_factory=ImageSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    subagent_models: ConfigDict = field(default_factory=dict)
    streaming: bool = True

    def with_model_config(self, model_config: ModelConfigDict | None) -> "SettingsSnapshot":
        """Return a session-equivalent snapshot with a different model config."""

        return replace(self, model=ModelSettings.from_dict(model_config))

    @classmethod
    def from_config_dicts(
        cls,
        *,
        model_config: ModelConfigDict | None,
        timeouts: TimeoutConfigDict | None = None,
        runner: RunnerConfigDict | None = None,
        compactor: CompactorConfigDict | None = None,
        display: DisplayConfigDict | None = None,
        tool: ToolConfigDict | None = None,
        team: TeamConfigSettingsDict | None = None,
        workflow: WorkflowConfigDict | None = None,
        web: WebConfigDict | None = None,
        mcp: McpConfigDict | None = None,
        image: ImageConfigDict | None = None,
        database: DatabaseConfigDict | None = None,
        subagent_models: ConfigDict | None = None,
        streaming: bool = True,
    ) -> "SettingsSnapshot":
        return cls(
            model=ModelSettings.from_dict(model_config),
            timeouts=TimeoutSettings.from_dict(timeouts),
            runner=RunnerSettings.from_dict(runner),
            compactor=CompactorSettings.from_dict(compactor),
            display=DisplaySettings.from_dict(display),
            tool=ToolSettings.from_dict(tool),
            team=TeamSettings.from_dict(team),
            workflow=WorkflowSettings.from_dict(workflow),
            web=WebSettings.from_dict(web),
            mcp=McpSettings.from_dict(mcp),
            image=ImageSettings.from_dict(image),
            database=DatabaseSettings.from_dict(database),
            subagent_models=copy.deepcopy(subagent_models or {}),
            streaming=bool(streaming),
        )
