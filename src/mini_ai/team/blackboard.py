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
        self._condition = threading.Condition(self._lock)  # 使用 Condition 替代 Event
        self._persist_path = persist_path
        self._max_entries = max_entries
        self._version = 0  # 版本号，用于检测变化
        if persist_path and persist_path.exists():
            self._load()

    def put(self, key: str, value: str, author: str = "") -> str:
        with self._condition:
            if key not in self._data and len(self._data) >= self._max_entries:
                oldest_key = min(self._data, key=lambda k: self._data[k].get("ts", 0))
                del self._data[oldest_key]
                logger.debug(f"[Blackboard] 淘汰最旧条目: {oldest_key}")
            self._data[key] = {
                "value": value,
                "author": author,
                "ts": time.time(),
            }
            self._version += 1  # 增加版本号
            self._persist()
            logger.info(f"[Blackboard] put {key} ({len(value)} chars) by {author or '?'}")
            self._condition.notify_all()  # 通知所有等待者
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
        with self._condition:
            self._data.clear()
            self._version += 1
            self._persist()
            self._condition.notify_all()

    def wait_for_change(self, timeout: float = 5.0) -> bool:
        """等待黑板数据变化，返回是否有变化。
        
        使用 Condition 替代 Event，避免竞态条件：
        - 在锁内检查版本号
        - 使用 condition.wait() 原子地释放锁并等待
        - 被唤醒后自动重新获取锁
        """
        with self._condition:
            current_version = self._version
            # 等待版本号变化
            result = self._condition.wait_for(
                lambda: self._version != current_version,
                timeout=timeout
            )
            return result
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
