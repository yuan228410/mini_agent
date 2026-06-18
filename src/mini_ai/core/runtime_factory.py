"""Session runtime construction helpers.

Adapters should assemble transport/UI objects themselves, then hand the shared
runtime ingredients to this module.  The factory owns the core boundary between
session identity, tool context, request context and the session-local registry.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

from ..config import DATABASE, DISPLAY, MODEL_CONFIG, RequestContext, RUNNER, STREAMING, TIMEOUTS, TOOL
from .runtime_context import SessionIdentity, SessionRuntimeContext, ToolContext
from .settings import SettingsSnapshot
from .tool_registry_factory import build_tool_registry


def build_session_runtime(
    *,
    identity: SessionIdentity,
    messages: list[dict],
    display: Any = None,
    history_db: Any = None,
    memory_store: Any = None,
    skill_loader: Any = None,
    subagent_loader: Any = None,
    bus: Any = None,
    team_mgr: Any = None,
    blackboard: Any = None,
    workflow_dirs: list[Path] | None = None,
    abort_event: Event | None = None,
    request_context: Any = None,
    model_config: dict | None = None,
    tool_registry: Any = None,
    mcp_loader: Any = None,
    compactor: Any = None,
    context_builder: Any = None,
    settings: SettingsSnapshot | None = None,
) -> SessionRuntimeContext:
    """Build a fully-bound runtime for one CLI/Web session.

    The returned runtime is explicit and session-local: tools are registered on a
    fresh ``ToolRegistry`` unless the caller intentionally supplies one.  No
    module-level tool registry is mutated by this factory.
    """

    tool_context = ToolContext(
        identity=identity,
        display=display,
        memory_store=memory_store,
        history_db=history_db,
        skill_loader=skill_loader,
        subagent_loader=subagent_loader,
        bus=bus,
        team_mgr=team_mgr,
        blackboard=blackboard,
        workflow_dirs=workflow_dirs,
        abort_event=abort_event,
    )
    registry = tool_registry or build_tool_registry(tool_context, mcp_loader=mcp_loader)
    snapshot = settings or SettingsSnapshot.from_config_dicts(
        model_config=model_config or MODEL_CONFIG,
        timeouts=TIMEOUTS,
        runner=RUNNER,
        display=DISPLAY,
        tool=TOOL,
        database=DATABASE,
        streaming=STREAMING,
    )
    req_ctx = request_context or RequestContext(model_config=snapshot.model.to_dict(), display=display)

    return SessionRuntimeContext(
        identity=identity,
        request_context=req_ctx,
        tool_registry=registry,
        tool_context=tool_context,
        messages=messages,
        settings=snapshot,
        compactor=compactor,
        context_builder=context_builder,
        history_db=history_db,
        memory_store=memory_store,
        skill_loader=skill_loader,
        bus=bus,
        team_mgr=team_mgr,
        blackboard=blackboard,
    )
