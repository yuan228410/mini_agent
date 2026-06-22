"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..application import chat_compact_service, chat_service, command_service, model_use_cases, plan_command_service, runtime_paths
from ..core.runtime_factory import build_request_context, build_session_runtime, build_settings_snapshot
from ..core.runtime_types import ModelConfigDict, RequestContextProtocol
from ..core.settings import SettingsSnapshot
from ..application.config_service import ConfigMutationDependencies, ConfigPreviewDependencies, ConfigToolLoaderDependencies
from ..application.model_use_cases import ModelRouteDependencies
from ..application.session_service import SessionServiceDependencies
from ..application.workspace_service import WorkspaceSwitchDependencies
from ..skills import SkillLoader
from ..workspace import WorkspaceManager

_WORKSPACE_MANAGERS: dict[str, WorkspaceManager] = {}


def current_settings_snapshot() -> SettingsSnapshot:
    """Capture the current configuration at the Web adapter boundary."""

    return build_settings_snapshot()


def active_model_name(settings: SettingsSnapshot | None = None) -> str:
    return model_use_cases.active_model_name(settings or current_settings_snapshot())


def available_model_names(settings: SettingsSnapshot | None = None) -> list[str]:
    return model_use_cases.available_model_names(settings or current_settings_snapshot())


def model_config_for_name(model_name: str | None, settings: SettingsSnapshot | None = None) -> ModelConfigDict | None:
    return model_use_cases.model_config_for_name(settings or current_settings_snapshot(), model_name)


def user_data_dir_for_settings(username: str, settings: SettingsSnapshot | None = None) -> Path:
    """Return a user data directory from runtime path settings."""

    return runtime_paths.user_data_dir_for(settings or current_settings_snapshot(), username)


def workspace_manager_for_user(username: str, settings: SettingsSnapshot | None = None) -> WorkspaceManager:
    """Build a workspace manager from runtime path settings."""

    return WorkspaceManager(user_data_dir_for_settings(username, settings), ensure_default=False)


def cached_workspace_manager_for_user(username: str, settings: SettingsSnapshot | None = None) -> WorkspaceManager:
    """Return a cached Web workspace manager for a user."""

    if settings is not None:
        return workspace_manager_for_user(username, settings)
    if username not in _WORKSPACE_MANAGERS:
        _WORKSPACE_MANAGERS[username] = workspace_manager_for_user(username)
    return _WORKSPACE_MANAGERS[username]


def workspace_switch_dependencies() -> WorkspaceSwitchDependencies:
    """Build Web workspace switch invalidation hooks at the adapter boundary."""

    from .session_manager import SessionManager
    from ..tools.cache import clear_tool_cache

    return WorkspaceSwitchDependencies(
        clear_tool_cache=clear_tool_cache,
        clear_workspace_sessions=SessionManager.instance().clear_workspace_prefix,
    )


def skill_loader_for_user_workspace(username: str = "", workspace: str = "", settings: SettingsSnapshot | None = None) -> SkillLoader:
    """Build a Web skill loader from runtime paths plus user/workspace tiers."""

    return runtime_paths.skill_loader_for_user_workspace(settings or current_settings_snapshot(), username, workspace)


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    return model_use_cases.settings_for_model(base_settings, model_name)


def request_context_for_settings(settings: SettingsSnapshot, display=None) -> RequestContextProtocol:
    return build_request_context(settings, display=display)


def config_preview_dependencies() -> ConfigPreviewDependencies:
    """Build Web config preview use-case dependencies at the adapter boundary."""

    from .session_manager import SessionManager, cache_key, get_or_create_components, get_or_create_session, resolve_base, _load_session_model
    from ..llm.base import estimate_tokens

    return ConfigPreviewDependencies(
        session_manager=SessionManager.instance(),
        cache_key=cache_key,
        resolve_base=resolve_base,
        get_or_create_session=get_or_create_session,
        get_or_create_components=get_or_create_components,
        load_session_model=_load_session_model,
        estimate_tokens=estimate_tokens,
    )


def config_tool_loader_dependencies() -> ConfigToolLoaderDependencies:
    """Build Web config tool-preview loader dependencies at the adapter boundary."""

    from .deps import SUBAGENT_LOADER, _MCP_LOADER

    return ConfigToolLoaderDependencies(subagent_loader=SUBAGENT_LOADER, mcp_loader=_MCP_LOADER)


def config_mutation_dependencies() -> ConfigMutationDependencies:
    """Build Web config mutation dependencies at the adapter boundary."""

    import mini_ai.config as cfg
    from ..config import _config_path, _raw, AVAILABLE_MODELS, switch_model
    from ..config import COMPACTOR, DISPLAY, LOGGING, PLAN, RUNNER, THINKING, TIMEOUTS, TOOL, WEB
    from .deps import _init_mcp

    return ConfigMutationDependencies(
        raw=_raw,
        config_path=_config_path,
        available_models=AVAILABLE_MODELS,
        switch_model=switch_model,
        reload_mcp=_init_mcp,
        section_globals={
            "thinking": THINKING,
            "display": DISPLAY,
            "compactor": COMPACTOR,
            "tool": TOOL,
            "runner": RUNNER,
            "plan": PLAN,
            "logging": LOGGING,
            "timeouts": TIMEOUTS,
            "web": WEB,
        },
        set_streaming=lambda value: setattr(cfg, "STREAMING", value),
    )


def mcp_status_dependencies() -> command_service.McpStatusDependencies:
    """Build Web MCP status dependencies at the adapter boundary."""

    from .deps import MCP_SETTINGS, _MCP_LOADER

    return command_service.McpStatusDependencies(settings=MCP_SETTINGS, loader=_MCP_LOADER)


def model_route_dependencies() -> ModelRouteDependencies:
    """Build Web model route use-case dependencies at the adapter boundary."""

    from .session_manager import SessionManager, cache_key, resolve_base, _load_session_model, _save_session_model

    return ModelRouteDependencies(
        session_models=SessionManager.instance(),
        cache_key=cache_key,
        resolve_base=resolve_base,
        load_session_model=_load_session_model,
        save_session_model=_save_session_model,
    )


def chat_session_dependencies() -> dict[str, Any]:
    """Return Web chat session helpers behind the adapter boundary."""

    from .session_manager import (
        SessionManager,
        cache_key,
        get_or_create_components,
        get_or_create_session,
        resolve_base,
        _load_session_name,
        _update_meta_cache,
    )

    return {
        "session_manager": SessionManager.instance(),
        "cache_key": cache_key,
        "resolve_base": resolve_base,
        "get_or_create_session": get_or_create_session,
        "get_or_create_components": get_or_create_components,
        "load_session_name": _load_session_name,
        "update_meta_cache": _update_meta_cache,
    }


def chat_rest_dependencies() -> chat_service.ChatRestDependencies:
    """Build Web chat REST dependencies at the adapter boundary."""

    from ..tools import inject_todos

    deps = chat_session_dependencies()
    return chat_service.ChatRestDependencies(
        session_manager=deps["session_manager"],
        cache_key=deps["cache_key"],
        resolve_base=deps["resolve_base"],
        get_or_create_session=deps["get_or_create_session"],
        get_or_create_components=deps["get_or_create_components"],
        load_session_name=deps["load_session_name"],
        update_meta_cache=deps["update_meta_cache"],
        inject_todos=inject_todos,
    )


def chat_runtime_dependencies() -> chat_service.ChatRuntimeDependencies:
    """Build Web chat runtime construction dependencies at the adapter boundary."""

    from .deps import SUBAGENT_LOADER, _MCP_LOADER
    from .session_manager import build_system_prompt

    return chat_service.ChatRuntimeDependencies(
        build_system_prompt=build_system_prompt,
        settings_for_model=settings_for_model,
        build_runtime=build_session_runtime,
        subagent_loader=SUBAGENT_LOADER,
        mcp_loader=_MCP_LOADER,
    )


def plan_command_dependencies() -> plan_command_service.PlanCommandDependencies:
    """Build Web plan command dependencies at the adapter boundary."""

    deps = chat_session_dependencies()
    return plan_command_service.PlanCommandDependencies(
        session_manager=deps["session_manager"],
        cache_key=deps["cache_key"],
        resolve_base=deps["resolve_base"],
        get_or_create_session=deps["get_or_create_session"],
        get_or_create_components=deps["get_or_create_components"],
    )


def chat_compact_dependencies() -> chat_compact_service.ChatCompactDependencies:
    """Build Web chat compaction dependencies at the adapter boundary."""

    from ..llm import chat

    deps = chat_session_dependencies()
    return chat_compact_service.ChatCompactDependencies(
        session_manager=deps["session_manager"],
        cache_key=deps["cache_key"],
        resolve_base=deps["resolve_base"],
        get_or_create_session=deps["get_or_create_session"],
        get_or_create_components=deps["get_or_create_components"],
        settings_for_model=settings_for_model,
        request_context_for_settings=request_context_for_settings,
        chat=chat,
    )


def team_component_for_user_workspace(username: str, workspace: str | None):
    """Return Web team components through the session-manager boundary."""

    from .session_manager import SessionManager, ws_key
    from ..application import team_service

    return team_service.get_team_component(SessionManager.instance(), ws_key, username, workspace)


def session_service_dependencies() -> SessionServiceDependencies:
    """Build Web session use-case dependencies at the adapter boundary."""

    from .session_manager import (
        SessionManager,
        build_system_prompt,
        cache_key,
        get_or_create_components,
        get_or_create_session,
        resolve_base,
        ws_key,
        _build_meta,
        _load_from_db,
        _save_session_name,
        _update_meta_cache,
    )
    from ..tools.update_todos import cleanup_session, get_todos, set_session

    return SessionServiceDependencies(
        session_manager=SessionManager.instance(),
        cache_key=cache_key,
        ws_key=ws_key,
        resolve_base=resolve_base,
        get_or_create_session=get_or_create_session,
        get_or_create_components=get_or_create_components,
        build_system_prompt=build_system_prompt,
        load_from_db=_load_from_db,
        build_meta=_build_meta,
        update_meta_cache=_update_meta_cache,
        save_session_name=_save_session_name,
        set_todo_session=set_session,
        cleanup_todo_session=cleanup_session,
        get_session_todos=get_todos,
    )
