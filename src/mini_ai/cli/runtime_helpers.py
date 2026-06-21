"""CLI runtime construction helpers.

Keep configuration capture at the CLI adapter boundary so rendering and
completion code can work from immutable settings snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib

import yaml

from ..core.runtime_factory import build_settings_snapshot
from ..core.settings import SettingsSnapshot
from ..skills import SkillLoader
from ..subagents import SubagentLoader


@dataclass(frozen=True, slots=True)
class ModelSwitchResult:
    ok: bool
    name: str
    model: str = "?"
    error: str = ""
    available: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceSwitchResult:
    ok: bool
    name: str
    error: str = ""


def user_data_dir_for_settings(username: str, settings: SettingsSnapshot | None = None) -> Path:
    """Return a CLI user data directory from runtime path settings."""

    runtime_settings = settings or current_settings_snapshot()
    data_dir = runtime_settings.paths.data_dir or Path.home() / ".mini_ai"
    user = username or "default"
    user_dir = data_dir / "users" / user
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def subagent_loader_for_settings(settings: SettingsSnapshot | None = None) -> SubagentLoader:
    """Build the CLI subagent loader from runtime package settings."""

    runtime_settings = settings or current_settings_snapshot()
    package_dir = runtime_settings.paths.package_dir or Path(__file__).resolve().parents[1]
    return SubagentLoader(package_dir / "subagents")


def context_builder_root(settings: SettingsSnapshot | None = None) -> Path:
    """Return the configured data root for context construction."""

    runtime_settings = settings or current_settings_snapshot()
    return runtime_settings.paths.data_dir or Path.home() / ".mini_ai"


def memory_roots_for_settings(username: str, ws_dir: Path, settings: SettingsSnapshot | None = None) -> tuple[Path, Path, Path]:
    """Return global, user and workspace memory roots from runtime settings."""

    runtime_settings = settings or current_settings_snapshot()
    data_dir = runtime_settings.paths.data_dir or Path.home() / ".mini_ai"
    user_dir = user_data_dir_for_settings(username, runtime_settings)
    return data_dir / "memory", user_dir / "memory", Path(ws_dir) / "memory_data"


def _config_module():
    return importlib.import_module("mini_ai.config")


def current_settings_snapshot() -> SettingsSnapshot:
    """Capture current configuration for CLI adapter helpers."""

    return build_settings_snapshot()


def available_model_names(settings: SettingsSnapshot | None = None) -> list[str]:
    """Return configured model names in config order."""

    runtime_settings = settings or current_settings_snapshot()
    return list(runtime_settings.model_configs.keys())


def active_model_label(settings: SettingsSnapshot | None = None) -> str:
    """Return the active provider model id for display."""

    runtime_settings = settings or current_settings_snapshot()
    return runtime_settings.model.model or "?"


def model_completion_items(settings: SettingsSnapshot | None = None) -> list[tuple[str, str]]:
    """Return slash-command completion entries for configured models."""

    runtime_settings = settings or current_settings_snapshot()
    active_name = runtime_settings.active_model_name
    items: list[tuple[str, str]] = []
    for name, config in runtime_settings.model_configs.items():
        model_id = runtime_settings.model.model if name == active_name else str(config.get("model", ""))
        items.append((f"/model {name}", model_id or ""))
    return items


def switch_active_model(name: str) -> ModelSwitchResult:
    """Persist the active model through the config-management boundary."""

    runtime_settings = current_settings_snapshot()
    available = tuple(runtime_settings.model_configs.keys())
    if name not in runtime_settings.model_configs:
        return ModelSwitchResult(ok=False, name=name, error=f"未知模型: {name}，可选: {', '.join(available)}", available=available)
    cfg = runtime_settings.model_config_for(name)
    if not cfg:
        return ModelSwitchResult(ok=False, name=name, error=f"模型配置无效: {name}", available=available)
    missing = [key for key in ("api_url", "api_key", "model") if key not in cfg]
    if missing:
        return ModelSwitchResult(ok=False, name=name, error=f"模型 '{name}' 缺少字段: {', '.join(missing)}", available=available)

    err = _config_module().switch_model(name)
    if err:
        return ModelSwitchResult(ok=False, name=name, error=err, available=available)
    return ModelSwitchResult(ok=True, name=name, model=str(cfg.get("model", "?")), available=available)


def persist_active_workspace(name: str) -> WorkspaceSwitchResult:
    """Persist the active workspace through the CLI config boundary."""

    cfg = _config_module()
    cfg._raw["active_workspace"] = name
    cfg._config_path.write_text(yaml.dump(cfg._raw, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    return WorkspaceSwitchResult(ok=True, name=name)


def active_workspace_name(default: str = "default") -> str:
    """Return the persisted active workspace name from the config boundary."""

    cfg = _config_module()
    return str(cfg._raw.get("active_workspace") or default)


def start_config_watcher() -> None:
    """Start config hot reload through the CLI config boundary."""

    _config_module().start_config_watcher()


def stop_config_watcher() -> None:
    """Stop config hot reload through the CLI config boundary."""

    _config_module().stop_config_watcher()


def skill_loader_for_settings(settings: SettingsSnapshot | None = None) -> SkillLoader:
    """Build the CLI skill loader from runtime path settings."""

    runtime_settings = settings or current_settings_snapshot()
    data_dir = runtime_settings.paths.data_dir or Path.home() / ".mini_ai"
    return SkillLoader(data_dir / "skills", runtime_settings.paths.skill_paths)
