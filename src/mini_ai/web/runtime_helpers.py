"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from ..core.runtime_factory import build_request_context, build_settings_snapshot
from ..core.runtime_types import ModelConfigDict, RequestContextProtocol
from ..core.settings import SettingsSnapshot


def current_settings_snapshot() -> SettingsSnapshot:
    """Capture the current configuration at the Web adapter boundary."""

    return build_settings_snapshot()


def active_model_name(settings: SettingsSnapshot | None = None) -> str:
    runtime_settings = settings or current_settings_snapshot()
    return runtime_settings.active_model_name


def available_model_names(settings: SettingsSnapshot | None = None) -> list[str]:
    runtime_settings = settings or current_settings_snapshot()
    return list(runtime_settings.model_configs.keys())


def model_config_for_name(model_name: str | None, settings: SettingsSnapshot | None = None) -> ModelConfigDict | None:
    runtime_settings = settings or current_settings_snapshot()
    return runtime_settings.model_config_for(model_name)


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    cfg = base_settings.model_config_for(model_name) if model_name else None
    return base_settings.with_model_config(cfg) if cfg else base_settings


def request_context_for_settings(settings: SettingsSnapshot, display=None) -> RequestContextProtocol:
    return build_request_context(settings, display=display)
