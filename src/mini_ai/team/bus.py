#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于文件 JSONL 的队友邮箱系统"""
import fcntl
import json
import re
import threading
import time
from pathlib import Path

from ..logger import logger
from .models import InboxMessage, InboxMessageDict, InboxMessageType

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_VALID_TYPES = {msg_type.value for msg_type in InboxMessageType}
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

    def close(self):
        """清理资源：清空唤醒回调与事件注册。"""
        with self._lock:
            self._on_send = None
            self._wake_events.clear()

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
        msg = InboxMessage(
            msg_type=InboxMessageType(msg_type),
            sender=sender,
            content=content,
            timestamp=time.time(),
        )
        inbox_path = self.dir / f"{to}.jsonl"
        with self._lock:
            if inbox_path.exists() and inbox_path.stat().st_size > _MAX_INBOX_CHARS:
                return f"Error: {to} 的 inbox 已满，请等待对方读取后再发送"
            with inbox_path.open("a", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            on_send = self._on_send
            ev = self._wake_events.get(to)
        if on_send:
            on_send(to)
        if ev:
            ev.set()
        return f"已送达 {to} 的 inbox"

    def read_inbox(self, name: str, peek: bool = False) -> list[InboxMessageDict]:
        """读取 inbox 消息。

        Args:
            name: 收件人名称
            peek: True=只读不清空（供并发读者检查），False=读取并清空（默认）

        多读者场景：先用 peek=True 检查，确认自己是唯一消费者后再 read_inbox(name) 清空。
        """
        name = name.strip()
        if not self._valid(name):
            return []
        inbox_path = self.dir / f"{name}.jsonl"
        with self._lock:
            try:
                if not inbox_path.exists():
                    return []
                with inbox_path.open("r+", encoding="utf-8") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        lines = f.read().splitlines()
                        if not peek:
                            f.seek(0)
                            f.truncate()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (OSError, FileNotFoundError):
                return []
        messages = []
        for line in lines:
            if not line.strip():
                continue
            try:
                messages.append(InboxMessage.from_dict(json.loads(line)).to_dict())
            except (json.JSONDecodeError, TypeError, ValueError):
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
        if sender != "lead":
            lr = self.send(sender, "lead", content, "broadcast")
            if not lr.startswith("Error"):
                count += 1
        return f"已广播给 {count} 位队友"
