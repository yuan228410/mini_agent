"""CLI runtime construction helpers.

Keep configuration capture at the CLI adapter boundary so rendering and
completion code can work from immutable settings snapshots.
"""
from __future__ import annotations

from pathlib import Path

from ..core.runtime_factory import build_settings_snapshot
from ..core.settings import SettingsSnapshot
from ..skills import SkillLoader


def current_settings_snapshot() -> SettingsSnapshot:
    """Capture current configuration for CLI adapter helpers."""

    return build_settings_snapshot()


def model_completion_items(settings: SettingsSnapshot | None = None) -> list[tuple[str, str]]:
    """Return slash-command completion entries for configured models."""

    runtime_settings = settings or current_settings_snapshot()
    active_name = runtime_settings.active_model_name
    items: list[tuple[str, str]] = []
    for name, config in runtime_settings.model_configs.items():
        model_id = runtime_settings.model.model if name == active_name else str(config.get("model", ""))
        items.append((f"/model {name}", model_id or ""))
    return items


def skill_loader_for_settings(settings: SettingsSnapshot | None = None) -> SkillLoader:
    """Build the CLI skill loader from runtime path settings."""

    runtime_settings = settings or current_settings_snapshot()
    data_dir = runtime_settings.paths.data_dir or Path.home() / ".mini_ai"
    return SkillLoader(data_dir / "skills", runtime_settings.paths.skill_paths)
