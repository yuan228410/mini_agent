"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from ..config import RequestContext, get_model_config
from ..core.settings import SettingsSnapshot


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    cfg = get_model_config(model_name) if model_name else None
    return base_settings.with_model_config(cfg) if cfg else base_settings


def request_context_for_settings(settings: SettingsSnapshot, display=None) -> RequestContext:
    return RequestContext(model_config=settings.model.to_dict(), display=display)
