"""Web config/model dependency builders."""
from __future__ import annotations

from ..application import command_service
from ..application.config_service import ConfigMutationDependencies, ConfigPreviewDependencies, ConfigToolLoaderDependencies
from ..application.model_use_cases import ModelRouteDependencies


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
