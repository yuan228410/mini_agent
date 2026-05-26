"""共享黑板 — Agent 间的键值状态存储，线程安全"""
import json
import threading
import time
from pathlib import Path

from ..logger import logger


class Blackboard:

    def __init__(self, persist_path: Path | None = None):
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        if persist_path and persist_path.exists():
            self._load()

    def put(self, key: str, value: str, author: str = "") -> str:
        with self._lock:
            self._data[key] = {
                "value": value,
                "author": author,
                "ts": time.time(),
            }
            self._persist()
        logger.info(f"[Blackboard] put {key} ({len(value)} chars) by {author or '?'}")
        return f"已写入 blackboard[{key}]（{len(value)} 字符）"

    def get(self, key: str, default: str = "") -> str:
        with self._lock:
            entry = self._data.get(key)
        if entry is None:
            logger.debug(f"[Blackboard] get {key} → miss")
            return default
        logger.debug(f"[Blackboard] get {key} → {len(entry['value'])} chars")
        return entry["value"]

    def list_keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
        return sorted(keys)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {k: v["value"] for k, v in self._data.items()}

    def clear(self):
        with self._lock:
            self._data.clear()
            self._persist()

    def render(self) -> str:
        with self._lock:
            if not self._data:
                return "黑板为空"
            lines = []
            for k, v in self._data.items():
                preview = v["value"][:100]
                author = f" (by {v['author']})" if v.get("author") else ""
                lines.append(f"  {k}{author}: {preview}")
            return "\n".join(lines)

    def _persist(self):
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self):
        try:
            self._data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}
