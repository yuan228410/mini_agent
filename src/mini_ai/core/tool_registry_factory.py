"""Session-local ToolRegistry construction.

Runtime paths should build explicit registries from ToolContext instead of mutating the
module-level compatibility registry in ``mini_ai.tools``.
"""
from __future__ import annotations

from .runtime_context import ToolContext
from ..config import DATA_DIR, PACKAGE_DIR
from ..tools import (
    ToolRegistry,
    read_file,
    read_image,
    write_file,
    edit_file,
    delete_file,
    rename_file,
    run_command,
    search_files,
    list_dir,
    web_fetch,
    update_todos,
    config_tool,
    delete_skill,
)


def build_tool_registry(tool_context: ToolContext, *, mcp_loader=None, base_tools: bool = True) -> ToolRegistry:
    """Build a fully-bound session-local registry from a ToolContext."""
    registry = ToolRegistry()
    registry._project_path = tool_context.identity.project_path or ""

    if base_tools:
        registry.add_tools(
            read_file,
            read_image,
            write_file,
            edit_file,
            delete_file,
            rename_file,
            run_command,
            search_files,
            list_dir,
            web_fetch,
            update_todos,
            config_tool,
            delete_skill,
        )

    if tool_context.memory_store is not None:
        registry.register_memory_tools(tool_context.memory_store)
    if tool_context.history_db is not None:
        registry.register_history_tools(tool_context.history_db, tool_context.identity.workspace or "default")
    if tool_context.skill_loader is not None:
        registry.register_skills(tool_context.skill_loader)
    if tool_context.subagent_loader is not None:
        registry.register_subagents(tool_context.subagent_loader)
    if mcp_loader is not None:
        registry.add_tools(*mcp_loader.get_tool_modules())
    if tool_context.bus is not None and tool_context.team_mgr is not None:
        registry.register_team(tool_context.bus, tool_context.team_mgr)
    if tool_context.blackboard is not None:
        workflow_dirs = tool_context.workflow_dirs or [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
        registry.register_blackboard(
            tool_context.blackboard,
            workflow_dirs=workflow_dirs,
            bus=tool_context.bus,
            manager=tool_context.team_mgr,
        )
    if tool_context.display is not None:
        registry.register_display(tool_context.display)
        if tool_context.team_mgr is not None and hasattr(tool_context.team_mgr, "set_display"):
            tool_context.team_mgr.set_display(tool_context.display)

    return registry
