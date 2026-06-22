"""Filesystem operation helpers for file service."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .file_types import EXT_LANG, IGNORE_DIRS, format_time


def list_files_at(root: Path, target: Path, path: str, max_items: int = 2000) -> dict[str, Any]:
    items = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return {"error": "权限不足"}

    truncated = False
    for entry in entries:
        if len(items) >= max_items:
            truncated = True
            break
        try:
            if entry.name in IGNORE_DIRS:
                continue
            if entry.name.startswith(".") and entry.is_dir():
                continue
            rel = str(entry.relative_to(root))
            if entry.is_dir():
                items.append({"name": entry.name, "type": "dir", "path": rel})
            else:
                st = entry.stat()
                lang = EXT_LANG.get(entry.suffix.lower(), "")
                items.append({
                    "name": entry.name, "type": "file", "path": rel,
                    "size": st.st_size, "language": lang,
                    "modified": format_time(st.st_mtime),
                })
        except (OSError, PermissionError):
            continue

    breadcrumb = []
    if path:
        parts = Path(path).parts
        for i, part in enumerate(parts):
            breadcrumb.append({"name": part, "path": str(Path(*parts[:i+1]))})

    return {
        "root": str(root),
        "current_path": path,
        "breadcrumb": breadcrumb,
        "items": items,
        "truncated": truncated,
    }


def read_text_window(target: Path, offset: int, limit: int) -> tuple[str, int, bool]:
    content_parts = []
    total_lines = 0
    end = offset + limit
    with target.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if offset <= total_lines < end:
                content_parts.append(line)
            total_lines += 1
    return "".join(content_parts), total_lines, end < total_lines


def search_files_at(root: Path, target: Path, query: str, max_results: int = 100, max_scanned: int = 20000, deadline_ms: int = 1500) -> dict[str, Any]:
    results = []
    query_lower = query.lower()
    scanned = 0
    truncated = False
    deadline = time.monotonic() + deadline_ms / 1000
    stack = [target]

    while stack:
        if scanned >= max_scanned or time.monotonic() > deadline:
            truncated = True
            break
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            scanned += 1
            if scanned >= max_scanned or time.monotonic() > deadline:
                truncated = True
                break
            try:
                if entry.name in IGNORE_DIRS:
                    continue
                is_dir = entry.is_dir()
                if is_dir and entry.name.startswith("."):
                    continue
                if is_dir:
                    stack.append(entry)
                if query_lower in entry.name.lower() and len(results) < max_results:
                    rel = str(entry.relative_to(root))
                    if is_dir:
                        results.append({"name": entry.name, "type": "dir", "path": rel})
                    else:
                        st = entry.stat()
                        lang = EXT_LANG.get(entry.suffix.lower(), "")
                        results.append({
                            "name": entry.name, "type": "file", "path": rel,
                            "size": st.st_size, "language": lang,
                            "modified": format_time(st.st_mtime),
                        })
                elif len(results) >= max_results:
                    truncated = True
                    break
            except (OSError, PermissionError):
                continue
    results.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {"results": results, "query": query, "scanned": scanned, "truncated": truncated}


def browse_dirs_at(root: Path) -> dict[str, Any]:
    parent = str(root.parent) if root != root.parent else ""
    dirs = []
    try:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in IGNORE_DIRS:
                try:
                    has_children = any(e.is_dir() for e in entry.iterdir())
                except (OSError, PermissionError):
                    has_children = False
                dirs.append({"name": entry.name, "path": str(entry), "has_children": has_children})
    except PermissionError:
        return {"error": "无权限访问", "current": str(root), "parent": parent, "dirs": []}
    return {"current": str(root), "parent": parent, "dirs": dirs}
