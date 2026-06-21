"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from pathlib import Path

from ..application import model_use_cases, runtime_paths
from ..core.runtime_factory import build_request_context, build_settings_snapshot
from ..core.runtime_types import ModelConfigDict, RequestContextProtocol
from ..core.settings import SettingsSnapshot
from ..skills import SkillLoader


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


def skill_loader_for_user_workspace(username: str = "", workspace: str = "", settings: SettingsSnapshot | None = None) -> SkillLoader:
    """Build a Web skill loader from runtime paths plus user/workspace tiers."""

    return runtime_paths.skill_loader_for_user_workspace(settings or current_settings_snapshot(), username, workspace)


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    return model_use_cases.settings_for_model(base_settings, model_name)


def request_context_for_settings(settings: SettingsSnapshot, display=None) -> RequestContextProtocol:
    return build_request_context(settings, display=display)
