"""Web runtime construction helpers.

Keep config adapter details out of Web route code while still allowing Web to select
models and create request contexts at the adapter boundary.
"""
from __future__ import annotations

from ..config import get_model_config
from ..core.runtime_factory import build_request_context
from ..core.runtime_types import RequestContextProtocol
from ..core.settings import SettingsSnapshot


def settings_for_model(base_settings: SettingsSnapshot, model_name: str | None) -> SettingsSnapshot:
    """Return settings with the requested model applied, preserving runtime budgets."""

    cfg = get_model_config(model_name) if model_name else None
    return base_settings.with_model_config(cfg) if cfg else base_settings


def request_context_for_settings(settings: SettingsSnapshot, display=None) -> RequestContextProtocol:
    return build_request_context(settings, display=display)
