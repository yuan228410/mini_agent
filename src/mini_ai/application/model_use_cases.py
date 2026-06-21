"""Model selection use cases shared by UI adapters."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..core.runtime_types import ModelConfigDict
from ..core.settings import SettingsSnapshot


@dataclass(frozen=True, slots=True)
class ModelOption:
    name: str
    model: str
    active: bool = False


class SessionModelStorePort(Protocol):
    def get_model(self, key: str) -> str: ...
    def set_model(self, key: str, model: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ModelRouteDependencies:
    session_models: SessionModelStorePort
    cache_key: Callable[[str, str | None, str], str]
    resolve_base: Callable[[str, str | None], Path]
    load_session_model: Callable[[Path | None, str], str]
    save_session_model: Callable[[Path | None, str, str], None]


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


def list_models(
    settings: SettingsSnapshot,
    *,
    deps: ModelRouteDependencies | None = None,
    session_id: str = "",
    workspace: str = "",
    username: str = "",
) -> dict[str, object]:
    """Return available models and the active model for an optional session."""

    result: dict[str, object] = {
        "active": settings.model.model or "?",
        "active_name": settings.active_model_name,
        "models": [{"name": n, "model": cfg.get("model", "?")} for n, cfg in settings.model_configs.items()],
    }
    if deps and session_id and username:
        session_model = session_model_name(deps, username=username, workspace=workspace, session_id=session_id)
        if session_model:
            result["active_name"] = session_model
            session_cfg = settings.model_config_for(session_model)
            result["active"] = session_cfg.get("model", "?") if session_cfg else "?"
    return result


def switch_session_model(
    settings: SettingsSnapshot,
    deps: ModelRouteDependencies,
    *,
    name: str,
    username: str,
    session_id: str,
    workspace: str = "",
) -> dict[str, object]:
    """Validate and persist a session-specific model selection."""

    if not username:
        return {"error": "缺少 username"}
    sid = session_id or "default"
    if name not in settings.model_configs:
        return {"error": f"未知模型: {name}，可选: {', '.join(settings.model_configs.keys())}"}
    cfg = settings.model_config_for(name)
    if not cfg:
        return {"error": f"模型配置无效: {name}"}

    ws = workspace or None
    key = deps.cache_key(username, ws, sid)
    deps.session_models.set_model(key, name)
    base = deps.resolve_base(username, ws)
    deps.save_session_model(base, sid, name)
    return {"status": "ok", "active_name": name, "model": cfg.get("model", "?")}


def session_model_name(deps: ModelRouteDependencies, *, username: str, workspace: str = "", session_id: str) -> str:
    """Return a session model from memory or persisted metadata."""

    ws = workspace or None
    key = deps.cache_key(username, ws, session_id)
    model_name = deps.session_models.get_model(key)
    if not model_name:
        base = deps.resolve_base(username, ws)
        model_name = deps.load_session_model(base, session_id)
    return model_name
