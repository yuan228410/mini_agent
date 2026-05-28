#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于文件 JSONL 的队友邮箱系统"""
import json
import re
import threading
import time
from pathlib import Path

from ..logger import logger

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_VALID_TYPES = {"message", "broadcast", "shutdown_request", "shutdown_response", "task_handoff"}
_MAX_INBOX_CHARS = 100000


class MessageBus:
    """文件邮箱：send 追加到 JSONL，read_inbox 读取并清空"""

    def __init__(self, inbox_dir: Path):
        self.dir = Path(inbox_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._on_send = None
        self._wake_events: dict[str, threading.Event] = {}

    def set_wake_callback(self, callback):
        with self._lock:
            self._on_send = callback

    def register_wake(self, name: str, event: threading.Event):
        with self._lock:
            self._wake_events[name] = event

    @staticmethod
    def _valid(name: str) -> bool:
        return bool(_NAME_RE.fullmatch(name))

    def send(self, sender: str, to: str, content: str, msg_type: str = "message") -> str:
        sender, to = sender.strip(), to.strip()
        if not self._valid(sender):
            return f"Error: invalid sender '{sender}'"
        if not self._valid(to):
            return f"Error: invalid receiver '{to}'"
        if msg_type not in _VALID_TYPES:
            return f"Error: invalid msg_type '{msg_type}'"

        logger.info(f"[MSG→] {sender} -> {to} ({msg_type})")
        logger.debug(f"[MSG详情] {sender} -> {to}: {content}")
        msg = {
            "type": msg_type, "from": sender,
            "content": content, "timestamp": time.time()
        }
        inbox_path = self.dir / f"{to}.jsonl"
        with self._lock:
            if inbox_path.exists() and inbox_path.stat().st_size > _MAX_INBOX_CHARS:
                return f"Error: {to} 的 inbox 已满，请等待对方读取后再发送"
            with inbox_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            on_send = self._on_send
            ev = self._wake_events.get(to)
        if on_send:
            on_send(to)
        if ev:
            ev.set()
        return f"已送达 {to} 的 inbox"

    def read_inbox(self, name: str) -> list[dict]:
        name = name.strip()
        if not self._valid(name):
            return []
        inbox_path = self.dir / f"{name}.jsonl"
        with self._lock:
            if not inbox_path.exists():
                return []
            lines = inbox_path.read_text(encoding="utf-8").splitlines()
            inbox_path.write_text("", encoding="utf-8")
        messages = []
        for line in lines:
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages

    def broadcast(self, sender: str, content: str, teammates: list[str]) -> str:
        count = 0
        for name in teammates:
            if name == sender:
                continue
            result = self.send(sender, name, content, "broadcast")
            if not result.startswith("Error"):
                count += 1
        return f"已广播给 {count} 位队友"
