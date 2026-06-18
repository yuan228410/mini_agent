"""Session-scoped runtime objects for agent execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .display_protocol import DisplayProtocol
from .runtime_types import (
    BlackboardProtocol,
    CompactorProtocol,
    ContextBuilderProtocol,
    HistoryDBProtocol,
    MemoryStoreProtocol,
    MessageBusProtocol,
    MessageDict,
    RequestContextProtocol,
    SkillLoaderProtocol,
    SubagentLoaderProtocol,
    TeamManagerProtocol,
    ToolRegistryProtocol,
)
from .settings import SettingsSnapshot


@dataclass
class SessionIdentity:
    username: str
    workspace: str
    session_id: str
    project_path: str = ""


@dataclass
class ToolContext:
    identity: SessionIdentity
    display: DisplayProtocol | None = None
    memory_store: MemoryStoreProtocol | None = None
    history_db: HistoryDBProtocol | None = None
    skill_loader: SkillLoaderProtocol | None = None
    subagent_loader: SubagentLoaderProtocol | None = None
    bus: MessageBusProtocol | None = None
    team_mgr: TeamManagerProtocol | None = None
    blackboard: BlackboardProtocol | None = None
    workflow_dirs: list[Path] | None = None
    abort_event: Event | None = None


@dataclass
class SessionRuntimeContext:
    identity: SessionIdentity
    request_context: RequestContextProtocol
    tool_registry: ToolRegistryProtocol
    tool_context: ToolContext
    messages: list[MessageDict]
    settings: SettingsSnapshot | None = None
    compactor: CompactorProtocol | None = None
    context_builder: ContextBuilderProtocol | None = None
    history_db: HistoryDBProtocol | None = None
    memory_store: MemoryStoreProtocol | None = None
    skill_loader: SkillLoaderProtocol | None = None
    bus: MessageBusProtocol | None = None
    team_mgr: TeamManagerProtocol | None = None
    blackboard: BlackboardProtocol | None = None

    def close(self) -> None:
        self.request_context.close()
