"""Web configuration preview use cases."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from ..core import build_session_runtime
from ..core.runtime_context import SessionIdentity
from ..core.runtime_types import MessageDict, ModelConfigDict, SessionComponents
from ..core.settings import SettingsSnapshot


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


def config_summary(
    settings: SettingsSnapshot,
    deps: ConfigPreviewDependencies,
    *,
    version: str,
    username: str,
    session_id: str = "",
    workspace: str = "",
) -> dict[str, Any]:
    """Return current session/model/token summary for the config panel."""

    if not username:
        return {"error": "缺少 username"}
    sid = session_id or "default"
    ws = workspace or None
    base = deps.resolve_base(username, ws)
    key = deps.cache_key(username, ws, sid)
    _, messages = deps.get_or_create_session(username, sid, base, ws, create=False)
    session_state = deps.session_manager.get(key)
    usage = getattr(session_state, "last_usage", None) or {"prompt_tokens": 0, "completion_tokens": 0}
    model_name = deps.session_manager.get_model(key) or deps.load_session_model(base, sid)
    model_cfg = settings.model_config_for(model_name) if model_name else settings.model.to_dict()
    if not model_cfg:
        model_cfg = settings.model.to_dict()
    system_prompt = messages[0]["content"] if messages else ""
    return {
        "version": version,
        "model": model_cfg.get("model", "?"),
        "context_length": model_cfg.get("context_length", 256000),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "system_prompt_tokens": deps.estimate_tokens(system_prompt) if system_prompt else 0,
        "history_count": len(messages) - 1 if messages else 0,
        "session_id": sid,
        "username": username,
    }


def system_prompt_preview(deps: ConfigPreviewDependencies, *, username: str, workspace: str = "") -> dict[str, Any]:
    """Build the system prompt preview for a user/workspace."""

    if not username:
        return {"error": "缺少 username"}
    ws = workspace or None
    base = deps.resolve_base(username, ws)
    comp = deps.get_or_create_components(username, "default", base, ws)
    ctx_builder = comp.get("ctx_builder")
    if not ctx_builder:
        return {"error": "ContextBuilder 不可用"}
    project_path = str(base) if base else ""
    system_prompt = ctx_builder.build(
        memory_store=comp.get("store"),
        skill_loader=comp.get("skill_loader"),
        project_path=project_path,
    )
    return {"system_prompt": system_prompt, "chars": len(system_prompt), "tokens": deps.estimate_tokens(system_prompt)}


def tools_preview(
    deps: ConfigPreviewDependencies,
    *,
    username: str = "",
    workspace: str = "",
    session_id: str = "default",
    subagent_loader=None,
    mcp_loader=None,
) -> dict[str, Any]:
    """Return current session tool definitions and token estimates."""

    user = username or "default"
    sid = session_id or "default"
    ws = workspace or None
    base = deps.resolve_base(user, ws)
    comp = deps.get_or_create_components(user, sid, base, ws)
    runtime = build_session_runtime(
        identity=SessionIdentity(
            username=user,
            workspace=workspace or "default",
            session_id=sid,
            project_path=comp.get("project_path") or "",
        ),
        messages=[],
        history_db=comp.get("history_db"),
        memory_store=comp.get("store"),
        skill_loader=comp.get("skill_loader"),
        subagent_loader=subagent_loader,
        bus=comp.get("bus"),
        team_mgr=comp.get("team_mgr"),
        blackboard=comp.get("blackboard"),
        mcp_loader=mcp_loader,
        compactor=comp.get("compactor"),
        context_builder=comp.get("ctx_builder"),
        settings=comp.get("settings"),
    )
    tools = [d for d in runtime.tool_registry.get_definitions() if d["function"]["name"] not in ("read_inbox", "list_teammates")]
    tools_json = json.dumps(tools, ensure_ascii=False)
    tool_names = [t.get("function", {}).get("name", "?") for t in tools]
    return {
        "tools": tools,
        "count": len(tools),
        "chars": len(tools_json),
        "tokens": deps.estimate_tokens(tools_json),
        "tool_names": tool_names,
    }


def _write_config(deps: ConfigMutationDependencies) -> None:
    with open(deps.config_path, "w", encoding="utf-8") as f:
        yaml.dump(deps.raw, f, default_flow_style=False, allow_unicode=True)


def settings_payload(deps: ConfigMutationDependencies) -> dict[str, Any]:
    """Return editable settings without exposing secret model fields."""

    models_safe = {}
    for name, cfg in deps.raw.get("models", {}).items():
        models_safe[name] = {
            "api_url": cfg.get("api_url", ""),
            "api_mode": cfg.get("api_mode", "openai"),
            "model": cfg.get("model", ""),
            "context_length": cfg.get("context_length", 256000),
            "temperature": cfg.get("temperature"),
            "max_tokens": cfg.get("max_tokens"),
            "top_p": cfg.get("top_p"),
            "reasoning_effort": cfg.get("reasoning_effort"),
            "thinking": cfg.get("thinking"),
        }
    return {
        "active_model": deps.raw.get("active_model", ""),
        "models": models_safe,
        "streaming": deps.raw.get("streaming", True),
        "thinking": deps.section_globals.get("thinking", {}),
        "display": deps.section_globals.get("display", {}),
        "compactor": deps.section_globals.get("compactor", {}),
        "timeouts": deps.section_globals.get("timeouts", {}),
        "runner": deps.section_globals.get("runner", {}),
        "plan": deps.section_globals.get("plan", {}),
        "tool": deps.section_globals.get("tool", {}),
        "web": deps.section_globals.get("web", {}),
        "logging": deps.section_globals.get("logging", {}),
    }


def update_settings(deps: ConfigMutationDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Apply config setting updates and persist changed sections."""

    updated_sections = []

    if "active_model" in body:
        name = body["active_model"]
        if name in deps.raw.get("models", {}):
            deps.raw["active_model"] = name
            deps.switch_model(name)
            updated_sections.append("active_model")

    if "thinking" in body:
        thinking = body["thinking"]
        if isinstance(thinking, dict):
            deps.raw.setdefault("thinking", {}).update(thinking)
            updated_sections.append("thinking")

    for section in ("display", "compactor", "tool", "runner", "plan", "logging", "timeouts", "web"):
        if section in body:
            section_update = body[section]
            if isinstance(section_update, dict):
                deps.raw.setdefault(section, {}).update(section_update)
                current = deps.section_globals.get(section)
                if current is not None:
                    current.update(section_update)
                updated_sections.append(section)

    if "streaming" in body:
        streaming = bool(body["streaming"])
        deps.raw["streaming"] = streaming
        deps.set_streaming(streaming)
        updated_sections.append("streaming")

    if "model_config" in body:
        model_updates = body["model_config"]
        if isinstance(model_updates, dict):
            model_name = model_updates.get("name", "")
            if model_name and model_name in deps.raw.get("models", {}):
                model_cfg = deps.raw["models"][model_name]
                for key in ("temperature", "max_tokens", "top_p", "reasoning_effort", "context_length"):
                    if key in model_updates:
                        val = model_updates[key]
                        if val is None:
                            model_cfg.pop(key, None)
                        else:
                            model_cfg[key] = val
                if "thinking" in model_updates:
                    thinking = model_updates["thinking"]
                    if thinking is None:
                        model_cfg.pop("thinking", None)
                    else:
                        model_cfg["thinking"] = thinking
                updated_sections.append(f"model_config.{model_name}")

    if updated_sections:
        _write_config(deps)

    return {"status": "ok", "updated": updated_sections}


def add_model(deps: ConfigMutationDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Add a model definition to config."""

    name = body.get("name", "").strip()
    if not name:
        return {"error": "模型名称不能为空"}
    if name in deps.raw.get("models", {}):
        return {"error": f"模型 '{name}' 已存在"}

    api_mode = body.get("api_mode", "openai")
    model_cfg = {
        "api_key": body.get("api_key", ""),
        "api_url": body.get("api_url", ""),
        "api_mode": api_mode,
        "model": body.get("model", ""),
        "context_length": body.get("context_length", 256000),
    }
    if body.get("temperature") is not None:
        model_cfg["temperature"] = body["temperature"]
    if body.get("headers"):
        model_cfg["headers"] = body["headers"]

    deps.raw.setdefault("models", {})[name] = model_cfg
    if name not in deps.available_models:
        deps.available_models.append(name)
    _write_config(deps)

    return {"status": "ok", "name": name}


def remove_model(deps: ConfigMutationDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Remove a model definition from config."""

    name = body.get("name", "").strip()
    if not name:
        return {"error": "模型名称不能为空"}
    if name not in deps.raw.get("models", {}):
        return {"error": f"模型 '{name}' 不存在"}
    if len(deps.raw.get("models", {})) <= 1:
        return {"error": "至少保留一个模型"}

    was_active = deps.raw.get("active_model") == name
    del deps.raw["models"][name]
    if name in deps.available_models:
        deps.available_models.remove(name)

    if was_active:
        first = next(iter(deps.raw["models"]))
        deps.raw["active_model"] = first
        deps.switch_model(first)

    _write_config(deps)
    return {"status": "ok", "removed": name, "new_active": deps.raw.get("active_model") if was_active else None}


def add_mcp_server(deps: ConfigMutationDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Add an MCP server definition and reload the Web MCP adapter."""

    name = body.get("name", "").strip()
    server_type = body.get("type", "stdio")

    if not name:
        return {"error": "服务器名称不能为空"}

    mcp_cfg = deps.raw.setdefault("mcp", {"enabled": True})
    servers = mcp_cfg.setdefault("servers", {})
    if name in servers:
        return {"error": f"MCP 服务器 '{name}' 已存在"}

    server_entry = {"type": server_type}
    if server_type == "stdio":
        command = body.get("command", "").strip()
        if not command:
            return {"error": "stdio 服务器需要 command"}
        server_entry["command"] = command
        if body.get("args"):
            server_entry["args"] = body["args"]
    elif server_type in ("streamable_http", "sse"):
        url = body.get("url", "").strip()
        if not url:
            return {"error": "HTTP 服务器需要 url"}
        server_entry["url"] = url
        if body.get("headers"):
            server_entry["headers"] = body["headers"]

    mcp_cfg["enabled"] = True
    servers[name] = server_entry
    _write_config(deps)

    try:
        deps.reload_mcp()
    except Exception:
        pass

    return {"status": "ok", "name": name}


def remove_mcp_server(deps: ConfigMutationDependencies, name: str) -> dict[str, Any]:
    """Remove an MCP server definition from config."""

    mcp_cfg = deps.raw.get("mcp", {})
    servers = mcp_cfg.get("servers", {})
    if name not in servers:
        return {"error": f"MCP 服务器 '{name}' 不存在"}

    del servers[name]
    _write_config(deps)
    return {"status": "ok", "removed": name}
