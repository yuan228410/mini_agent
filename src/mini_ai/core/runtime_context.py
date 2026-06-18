"""Session-scoped runtime objects for agent execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any


@dataclass
class SessionIdentity:
    username: str
    workspace: str
    session_id: str
    project_path: str = ""


@dataclass
class ToolContext:
    identity: SessionIdentity
    display: Any = None
    memory_store: Any = None
    history_db: Any = None
    skill_loader: Any = None
    subagent_loader: Any = None
    bus: Any = None
    team_mgr: Any = None
    blackboard: Any = None
    workflow_dirs: list[Path] | None = None
    abort_event: Event | None = None


@dataclass
class SessionRuntimeContext:
    identity: SessionIdentity
    request_context: Any
    tool_registry: Any
    tool_context: ToolContext
    messages: list[dict]
    compactor: Any = None
    context_builder: Any = None
    history_db: Any = None
    memory_store: Any = None
    skill_loader: Any = None
    bus: Any = None
    team_mgr: Any = None
    blackboard: Any = None

    def close(self):
        if self.request_context and hasattr(self.request_context, "close"):
            self.request_context.close()
