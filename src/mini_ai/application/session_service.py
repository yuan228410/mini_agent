"""Session management use cases shared by Web adapters."""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from ..core.runtime_types import MessageDict
from ..logger import logger
from ..tools import inject_todos as _inject_todos


class SessionManagerPort(Protocol):
    def create_session(self, key: str, messages: list[MessageDict]) -> None: ...
    def get_messages(self, key: str) -> list[MessageDict] | None: ...
    def get_meta(self, key: str) -> dict[str, Any] | None: ...
    def set_meta(self, key: str, meta: dict[str, Any]) -> None: ...
    def get_status(self, key: str) -> str: ...
    def delete_session(self, key: str) -> None: ...
    def get_team_component(self, key: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class SessionServiceDependencies:
    session_manager: SessionManagerPort
    cache_key: Callable[[str, str | None, str], str]
    ws_key: Callable[[str, str | None], str]
    resolve_base: Callable[[str, str | None], Path]
    get_or_create_session: Callable[..., tuple[Any, list[MessageDict]]]
    get_or_create_components: Callable[[str, str, Path | None, str | None], dict[str, Any]]
    build_system_prompt: Callable[[str, str, Path | None, str | None], str]
    load_from_db: Callable[[str, str, Path | None, str | None], list[MessageDict] | None]
    build_meta: Callable[[str, list[MessageDict], str, str | None], dict[str, Any]]
    update_meta_cache: Callable[[str, str, str | None, list[MessageDict] | None], None]
    save_session_name: Callable[[Path | None, str, str], None]
    set_todo_session: Callable[[str], None]
    cleanup_todo_session: Callable[[str], None]
    get_session_todos: Callable[[str], list[dict[str, Any]]]


def create_session(deps: SessionServiceDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Create a new chat session and persist its initial system message."""

    username = body.get("username", "")
    workspace = body.get("workspace") or "default"
    if not username:
        return {"error": "缺少 username"}
    logger.info(f"[session] 用户主动创建会话 ws={workspace}")
    base = deps.resolve_base(username, workspace)
    sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
    key = deps.cache_key(username, workspace, sid)
    system_prompt = deps.build_system_prompt(username, sid, base, workspace)

    deps.session_manager.create_session(key, [{"role": "system", "content": system_prompt, "name": "新会话"}])
    deps.set_todo_session(key)
    msgs = deps.session_manager.get_messages(key)
    if msgs:
        _inject_todos(msgs)
    deps.update_meta_cache(username, sid, workspace, msgs)

    comp = deps.get_or_create_components(username, sid, base, workspace)
    comp["history_db"].append(workspace, sid, "system", system_prompt, metadata=json.dumps({"name": "新会话"}))
    return {"session_id": sid}


def list_sessions(deps: SessionServiceDependencies, username: str, workspace: str | None = None) -> dict[str, Any]:
    """Return session metadata for a user workspace, creating a default session if needed."""

    try:
        base = deps.resolve_base(username, workspace)
    except ValueError:
        return {"sessions": []}
    if not base.exists():
        return {"sessions": []}

    sm = deps.session_manager
    sessions = []
    for d in sorted(base.iterdir(), key=lambda d: d.name, reverse=True):
        if not d.is_dir() or d.name.startswith("."):
            continue
        sid = d.name
        key = deps.cache_key(username, workspace, sid)
        cached = sm.get_meta(key)
        if cached:
            cached = dict(cached)
            cached["status"] = sm.get_status(key)
            sessions.append(cached)
            continue
        msgs = deps.load_from_db(username, sid, base, workspace) or []
        meta = deps.build_meta(sid, msgs, username, workspace)
        sm.set_meta(key, meta)
        sessions.append(meta)

    if not sessions and (workspace is None or workspace == "default"):
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        key = deps.cache_key(username, workspace, sid)
        system_prompt = deps.build_system_prompt(username, sid, base, workspace)
        sm.create_session(key, [{"role": "system", "content": system_prompt, "name": "新会话"}])
        deps.update_meta_cache(username, sid, workspace, sm.get_messages(key))
        comp = deps.get_or_create_components(username, sid, base, workspace)
        comp["history_db"].append(workspace or "default", sid, "system", system_prompt, metadata=json.dumps({"name": "新会话"}))
        sessions.append(deps.build_meta(sid, sm.get_messages(key), username, workspace))
        logger.info(f"[session] default 工作空间无会话，自动创建 sid={sid}")

    sessions.sort(key=lambda s: s.get("updated_at", "") or s.get("created_at", ""), reverse=True)
    return {"sessions": sessions}


def get_todos(deps: SessionServiceDependencies, username: str, workspace: str | None, session_id: str) -> dict[str, Any]:
    """Return todos for a session."""

    return {"todos": deps.get_session_todos(deps.cache_key(username, workspace, session_id))}


def title_from_user_content(content: Any, *, limit: int = 50) -> str:
    """Derive a compact session title from a user message payload."""

    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        return " ".join(text_parts)[:limit]
    return str(content or "")[:limit]


def maybe_auto_name_session(
    messages: list[MessageDict],
    *,
    base: Path | None,
    session_id: str,
    save_session_name: Callable[[Path | None, str, str], None],
) -> str | None:
    """Name a new session from its first visible user message when appropriate."""

    if not messages or messages[0].get("name", "") not in ("", "新会话"):
        return None
    user_msgs = [m for m in messages if m.get("role") == "user" and not m.get("_internal")]
    if len(user_msgs) != 1:
        return None

    auto_name = title_from_user_content(user_msgs[0].get("content", ""))
    if not auto_name:
        return None

    messages[0]["name"] = auto_name
    save_session_name(base, session_id, auto_name)
    return auto_name


def delete_session(deps: SessionServiceDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Delete one session and clean associated cached state."""

    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        return {"error": "缺少 session_id"}
    ws = workspace or None
    key = deps.cache_key(username, ws, session_id)

    try:
        base = deps.resolve_base(username, ws)
        comp = deps.get_or_create_components(username, session_id, base, ws)
        comp["history_db"].delete_session(ws or "default", session_id)
    except Exception:
        pass

    sm = deps.session_manager
    sm.delete_session(key)

    wk = deps.ws_key(username, ws)
    remaining = any(sm.get_messages(k) is not None for k in getattr(sm, "_sessions", {}) if k.startswith(f"{username}:{ws or 'default'}:") and k != key)
    if not remaining:
        team_comp = sm.get_team_component(wk)
        if team_comp:
            team_mgr = team_comp.get("team_mgr")
            if team_mgr:
                for name in team_mgr.active_member_names():
                    team_comp["bus"].send("lead", name, "会话结束，请退出。", "shutdown_request")

    deps.cleanup_todo_session(key)
    session_dir = deps.resolve_base(username, ws) / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
    return {"status": "ok"}


def batch_delete_sessions(deps: SessionServiceDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Delete multiple sessions and clean associated cached state."""

    username = body.get("username", "")
    session_ids = body.get("session_ids", [])
    workspace = body.get("workspace", "") or "default"
    if not username or not session_ids:
        return {"error": "参数不完整", "deleted": 0}
    ws = workspace or None
    base = deps.resolve_base(username, ws)
    sm = deps.session_manager
    deleted = 0

    for sid in session_ids:
        key = deps.cache_key(username, ws, sid)
        try:
            comp = deps.get_or_create_components(username, sid, base, ws)
            comp["history_db"].delete_session(workspace or "default", sid)
        except Exception:
            pass
        sm.delete_session(key)
        deps.cleanup_todo_session(key)
        session_dir = base / sid
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        deleted += 1
    return {"status": "ok", "deleted": deleted}


def rename_session(deps: SessionServiceDependencies, body: dict[str, Any]) -> dict[str, Any]:
    """Rename a session and update cached/file metadata."""

    username = body.get("username", "")
    session_id = body.get("session_id", "")
    name = body.get("name", "").strip()
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id or not name:
        return {"error": "参数不完整"}
    ws = workspace or None
    base = deps.resolve_base(username, ws)
    _, messages = deps.get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    messages[0]["name"] = name
    deps.update_meta_cache(username, session_id, ws, messages)
    deps.save_session_name(base, session_id, name)
    return {"status": "ok", "name": name}
