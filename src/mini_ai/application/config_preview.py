"""Configuration preview payload builders."""
from __future__ import annotations

import json
from typing import Any

from ..core import build_session_runtime
from ..core.runtime_context import SessionIdentity
from ..core.settings import SettingsSnapshot


def config_summary(
    settings: SettingsSnapshot,
    deps: Any,
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


def system_prompt_preview(deps: Any, *, username: str, workspace: str = "") -> dict[str, Any]:
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
    deps: Any,
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
