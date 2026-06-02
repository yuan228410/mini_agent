"""共享黑板 — Agent 间的键值状态存储，线程安全"""
import json
import threading
import time
from pathlib import Path

from ..logger import logger


_MAX_ENTRIES = 500

class Blackboard:

    def __init__(self, persist_path: Path | None = None, max_entries: int = _MAX_ENTRIES):
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._max_entries = max_entries
        self._change_event = threading.Event()
        self._change_event.set()  # 初始为 set，首次 wait_for_change 不超时
        if persist_path and persist_path.exists():
            self._load()

    def put(self, key: str, value: str, author: str = "") -> str:
        with self._lock:
            if key not in self._data and len(self._data) >= self._max_entries:
                oldest_key = min(self._data, key=lambda k: self._data[k].get("ts", 0))
                del self._data[oldest_key]
                logger.debug(f"[Blackboard] 淘汰最旧条目: {oldest_key}")
            self._data[key] = {
                "value": value,
                "author": author,
                "ts": time.time(),
            }
            self._persist()
            logger.info(f"[Blackboard] put {key} ({len(value)} chars) by {author or '?'}")
            self._change_event.set()
            return f"已写入 blackboard[{key}]（{len(value)} 字符）"

    def get(self, key: str, default: str | object = ""):
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

    def snapshot(self, detailed: bool = False) -> dict:
        with self._lock:
            if detailed:
                return {k: {"value": v["value"], "author": v.get("author", ""), "ts": v.get("ts", 0)} for k, v in self._data.items()}
            return {k: v["value"] for k, v in self._data.items()}

    def clear(self):
        with self._lock:
            self._data.clear()
            self._persist()
            self._change_event.set()

    def wait_for_change(self, timeout: float = 5.0) -> bool:
        """等待黑板数据变化，返回是否有变化。"""
        with self._lock:
            if self._change_event.is_set():
                self._change_event.clear()
                return True
        # 不在锁内 clear — 让 event 保持 set 直到 wait 消费，
        # 避免 clear 后、wait 前 put() 的 set 被遗漏
        return self._change_event.wait(timeout=timeout)
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
        """原子写入文件：先写临时文件，再替换，避免写入过程中崩溃导致文件损坏"""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            # 写入临时文件
            temp_path = self._persist_path.with_suffix('.tmp')
            temp_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 原子替换（跨平台）
            import os
            os.replace(str(temp_path), str(self._persist_path))
        except OSError as e:
            logger.warning(f"[Blackboard] 持久化失败: {e}")

    def _load(self):
        try:
            self._data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}
