"""Configuration mutation helpers."""
from __future__ import annotations

from typing import Any

import yaml


def write_config(deps: Any) -> None:
    with open(deps.config_path, "w", encoding="utf-8") as f:
        yaml.dump(deps.raw, f, default_flow_style=False, allow_unicode=True)


def settings_payload(deps: Any) -> dict[str, Any]:
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


def update_settings(deps: Any, body: dict[str, Any]) -> dict[str, Any]:
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
        _update_model_config(deps, body["model_config"], updated_sections)

    if updated_sections:
        write_config(deps)

    return {"status": "ok", "updated": updated_sections}


def _update_model_config(deps: Any, model_updates: Any, updated_sections: list[str]) -> None:
    if not isinstance(model_updates, dict):
        return
    model_name = model_updates.get("name", "")
    if not model_name or model_name not in deps.raw.get("models", {}):
        return
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


def add_model(deps: Any, body: dict[str, Any]) -> dict[str, Any]:
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
    write_config(deps)

    return {"status": "ok", "name": name}


def remove_model(deps: Any, body: dict[str, Any]) -> dict[str, Any]:
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

    write_config(deps)
    return {"status": "ok", "removed": name, "new_active": deps.raw.get("active_model") if was_active else None}


def add_mcp_server(deps: Any, body: dict[str, Any]) -> dict[str, Any]:
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
    write_config(deps)

    try:
        deps.reload_mcp()
    except Exception:
        pass

    return {"status": "ok", "name": name}


def remove_mcp_server(deps: Any, name: str) -> dict[str, Any]:
    """Remove an MCP server definition from config."""

    mcp_cfg = deps.raw.get("mcp", {})
    servers = mcp_cfg.get("servers", {})
    if name not in servers:
        return {"error": f"MCP 服务器 '{name}' 不存在"}

    del servers[name]
    write_config(deps)
    return {"status": "ok", "removed": name}
