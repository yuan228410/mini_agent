"""Web chat dependency builders."""
from __future__ import annotations

from typing import Any

from ..application import chat_compact_service, chat_service, plan_command_service
from .runtime_helpers import request_context_for_settings, settings_for_model


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
    from ..core.runtime_factory import build_session_runtime

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
