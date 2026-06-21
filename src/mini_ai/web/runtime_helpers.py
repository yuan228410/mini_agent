"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from pathlib import Path

from ..core.runtime_factory import build_request_context, build_settings_snapshot
from ..core.runtime_types import ModelConfigDict, RequestContextProtocol
from ..core.settings import SettingsSnapshot
from ..skills import SkillLoader
from ..workspace import WorkspaceManager


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


def user_data_dir_for_settings(username: str, settings: SettingsSnapshot | None = None) -> Path:
    """Return a user data directory from runtime path settings."""

    runtime_settings = settings or current_settings_snapshot()
    data_dir = runtime_settings.paths.data_dir or Path.home() / ".mini_ai"
    user = username or "default"
    user_dir = data_dir / "users" / user
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def skill_loader_for_user_workspace(username: str = "", workspace: str = "", settings: SettingsSnapshot | None = None) -> SkillLoader:
    """Build a Web skill loader from runtime paths plus user/workspace tiers."""

    runtime_settings = settings or current_settings_snapshot()
    data_dir = runtime_settings.paths.data_dir or Path.home() / ".mini_ai"
    user_dir = user_data_dir_for_settings(username, runtime_settings)
    user_skills_dir = user_dir / "skills"
    ws_skills_dir = None
    if workspace:
        ws_mgr = WorkspaceManager(user_dir, ensure_default=False)
        ws = ws_mgr.get(workspace)
        if ws:
            ws_skills_dir = ws.ws_dir / "skills"
    return SkillLoader(
        data_dir / "skills",
        runtime_settings.paths.skill_paths,
        user_skills_dir=user_skills_dir,
        workspace_skills_dir=ws_skills_dir,
    )


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    cfg = base_settings.model_config_for(model_name) if model_name else None
    return base_settings.with_model_config(cfg) if cfg else base_settings


def request_context_for_settings(settings: SettingsSnapshot, display=None) -> RequestContextProtocol:
    return build_request_context(settings, display=display)
