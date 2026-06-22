"""Web session metadata persistence helpers."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..core.runtime_types import MessageDict
from .route_types import SessionMeta


def parse_created_at(sid: str) -> str:
    """Parse a timestamp prefix from a session id."""

    try:
        dt = datetime.strptime(sid[:15], "%Y%m%d-%H%M%S")
        return dt.isoformat()
    except (ValueError, IndexError):
        return ""


def load_session_name(base: Path | None, sid: str) -> str:
    """Load a persisted session display name."""

    return _read_meta_value(base, sid, "name")


def save_session_name(base: Path | None, sid: str, name: str) -> None:
    """Persist a session display name."""

    _write_meta_value(base, sid, "name", name)


def load_session_model(base: Path | None, sid: str) -> str:
    """Load a persisted session model name."""

    return _read_meta_value(base, sid, "model")


def save_session_model(base: Path | None, sid: str, model_name: str) -> None:
    """Persist a session model name."""

    _write_meta_value(base, sid, "model", model_name)


def build_meta(
    sid: str,
    messages: list[MessageDict],
    username: str,
    workspace: str | None = None,
    *,
    resolve_base: Callable[[str, str | None], Path],
    get_model: Callable[[str], str],
    get_status: Callable[[str], str],
    cache_key: Callable[[str, str | None, str], str],
) -> SessionMeta:
    """Build route metadata for one Web chat session."""

    key = cache_key(username, workspace, sid)
    non_system = [m for m in messages if m["role"] != "system"]
    first_user = next((m.get("content", "")[:50] for m in non_system if m["role"] == "user"), "")
    name = ""
    try:
        base = resolve_base(username, workspace or "default")
        name = load_session_name(base, sid)
    except Exception:
        pass
    if not name:
        name = messages[0].get("name", "") if messages else ""
    if not name:
        name = first_user or "新会话"
    created_at = parse_created_at(sid)
    return {
        "session_id": sid,
        "name": name,
        "model": get_model(key),
        "message_count": len(non_system),
        "preview": first_user,
        "created_at": created_at,
        "updated_at": next((m.get("timestamp", "") for m in reversed(non_system) if m.get("timestamp")), created_at),
        "status": get_status(key),
    }


def _read_meta_value(base: Path | None, sid: str, key: str) -> str:
    if not base:
        return ""
    meta_path = base / sid / "meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get(key, "")
        except Exception:
            pass
    return ""


def _write_meta_value(base: Path | None, sid: str, key: str, value: str) -> None:
    if not base:
        return
    meta_path = base / sid / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta[key] = value
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
