"""Web configuration preview use cases."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from ..core.runtime_types import MessageDict, SessionComponents
from ..core.settings import SettingsSnapshot
from .config_mutation import add_mcp_server, add_model, remove_mcp_server, remove_model, settings_payload, update_settings, write_config
from .config_preview import config_summary, system_prompt_preview, tools_preview


class ConfigSessionStorePort(Protocol):
    def get(self, key: str) -> Any | None: ...
    def get_model(self, key: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ConfigToolLoaderDependencies:
    subagent_loader: Any | None = None
    mcp_loader: Any | None = None


@dataclass(frozen=True, slots=True)
class ConfigMutationDependencies:
    raw: dict[str, Any]
    config_path: Path
    available_models: list[str]
    switch_model: Callable[[str], None]
    reload_mcp: Callable[[], None]
    section_globals: dict[str, dict[str, Any]]
    set_streaming: Callable[[bool], None]


@dataclass(frozen=True, slots=True)
class ConfigPreviewDependencies:
    session_manager: ConfigSessionStorePort
    cache_key: Callable[[str, str | None, str], str]
    resolve_base: Callable[[str, str | None], Path]
    get_or_create_session: Callable[..., tuple[str, list[MessageDict] | None]]
    get_or_create_components: Callable[[str, str, Path | None, str | None], SessionComponents]
    load_session_model: Callable[[Path | None, str], str]
    estimate_tokens: Callable[[str], int]
