"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..application import model_use_cases, runtime_paths
from ..core.runtime_factory import build_request_context, build_settings_snapshot
from ..core.runtime_types import ModelConfigDict, RequestContextProtocol
from ..core.settings import SettingsSnapshot
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
