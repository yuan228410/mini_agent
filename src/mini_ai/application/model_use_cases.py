"""Model selection use cases shared by UI adapters."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.runtime_types import ModelConfigDict
from ..core.settings import SettingsSnapshot


@dataclass(frozen=True, slots=True)
class ModelOption:
    name: str
    model: str
    active: bool = False


def active_model_name(settings: SettingsSnapshot) -> str:
    """Return the configured active model alias."""

    return settings.active_model_name


def active_model_label(settings: SettingsSnapshot) -> str:
    """Return the active provider model id for display."""

    return settings.model.model or "?"


def available_model_names(settings: SettingsSnapshot) -> list[str]:
    """Return configured model aliases in config order."""

    return list(settings.model_configs.keys())


def model_config_for_name(settings: SettingsSnapshot, model_name: str | None) -> ModelConfigDict | None:
    """Return a copied model config for a configured model alias."""

    return settings.model_config_for(model_name)


def model_options(settings: SettingsSnapshot) -> list[ModelOption]:
    """Return configured model aliases with display model ids."""

    active_name = settings.active_model_name
    options: list[ModelOption] = []
    for name, config in settings.model_configs.items():
        model_id = settings.model.model if name == active_name else str(config.get("model", ""))
        options.append(ModelOption(name=name, model=model_id or "", active=name == active_name))
    return options


def model_completion_items(settings: SettingsSnapshot) -> list[tuple[str, str]]:
    """Return slash-command completion entries for configured models."""

    return [(f"/model {option.name}", option.model) for option in model_options(settings)]


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    cfg = base_settings.model_config_for(model_name) if model_name else None
    return base_settings.with_model_config(cfg) if cfg else base_settings
