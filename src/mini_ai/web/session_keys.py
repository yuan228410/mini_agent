"""Web session key helpers."""
from __future__ import annotations


def cache_key(username: str, workspace: str | None, sid: str) -> str:
    return f"{username}:{workspace or 'default'}:{sid}"


def ws_key(username: str, workspace: str | None) -> str:
    return f"{username}:{workspace or 'default'}"
