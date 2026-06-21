"""Session runtime construction helpers.

Adapters should assemble transport/UI objects themselves, then hand the shared
runtime ingredients to this module.  The factory owns the core boundary between
session identity, tool context, request context and the session-local registry.
"""
from __future__ import annotations

from pathlib import Path
from threading import Event

from ..config import DATABASE, DISPLAY, MODEL_CONFIG, RequestContext, RUNNER, STREAMING, TIMEOUTS, TOOL
from .display_protocol import DisplayProtocol
from .runtime_context import DerivedAgentResources, SessionIdentity, SessionRuntimeContext, ToolContext
from .runtime_types import (
    BlackboardProtocol,
    CompactorProtocol,
    ContextBuilderProtocol,
    HistoryDBProtocol,
    McpLoaderProtocol,
    MemoryStoreProtocol,
    MessageBusProtocol,
    MessageDict,
    ModelConfigDict,
    RequestContextProtocol,
    SkillLoaderProtocol,
    SubagentLoaderProtocol,
    TeamManagerProtocol,
    ToolRegistryProtocol,
)
from .settings import SettingsSnapshot
from .tool_registry_factory import build_tool_registry


def build_session_runtime(
    *,
    identity: SessionIdentity,
    messages: list[MessageDict],
    display: DisplayProtocol | None = None,
    history_db: HistoryDBProtocol | None = None,
    memory_store: MemoryStoreProtocol | None = None,
    skill_loader: SkillLoaderProtocol | None = None,
    subagent_loader: SubagentLoaderProtocol | None = None,
    bus: MessageBusProtocol | None = None,
    team_mgr: TeamManagerProtocol | None = None,
    blackboard: BlackboardProtocol | None = None,
    workflow_dirs: list[Path] | None = None,
    abort_event: Event | None = None,
    request_context: RequestContextProtocol | None = None,
    model_config: ModelConfigDict | None = None,
    tool_registry: ToolRegistryProtocol | None = None,
    mcp_loader: McpLoaderProtocol | None = None,
    compactor: CompactorProtocol | None = None,
    context_builder: ContextBuilderProtocol | None = None,
    settings: SettingsSnapshot | None = None,
) -> SessionRuntimeContext:
    """Build a fully-bound runtime for one CLI/Web session.

    The returned runtime is explicit and session-local: tools are registered on a
    fresh ``ToolRegistry`` unless the caller intentionally supplies one.  No
    module-level tool registry is mutated by this factory.
    """

    snapshot = settings or SettingsSnapshot.from_config_dicts(
        model_config=model_config or MODEL_CONFIG,
        timeouts=TIMEOUTS,
        runner=RUNNER,
        display=DISPLAY,
        tool=TOOL,
        database=DATABASE,
        streaming=STREAMING,
    )
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
        compactor=compactor,
        context_builder=context_builder,
        mcp_loader=mcp_loader,
        settings=snapshot,
    )
    registry = tool_registry or build_tool_registry(tool_context, mcp_loader=mcp_loader)
    resources = DerivedAgentResources(
        identity=identity,
        tool_registry=registry,
        subagent_loader=subagent_loader,
        skill_loader=skill_loader,
        context_builder=context_builder,
        compactor=compactor,
        abort_event=abort_event,
        mcp_loader=mcp_loader,
        settings=snapshot,
    )
    bind_resources = getattr(registry, "bind_derived_agent_resources", None)
    if callable(bind_resources):
        bind_resources(resources)
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
        mcp_loader=mcp_loader,
        derived_agent_resources=resources,
    )
