#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持久队友管理：spawn、状态追踪、线程循环"""
import json
import re
import threading
from pathlib import Path

from .config import DATA_DIR, TIMEOUTS, TEAMMATE, MODEL_CONFIG
from .logger import logger

_BASE_TOOL_NAMES = tuple(TEAMMATE["base_tools"])
_MAX_TEAMMATES = TEAMMATE["max_teammates"]


class TeammateManager:
    """队友持久化管理，配置持久到 team_config.json"""

    def __init__(self, *, team_dir: Path, bus, project_dir: Path):
        self.dir = Path(team_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "team_config.json"
        self.bus = bus
        self.project_dir = project_dir
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}
        self._wake_events: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self._mark_offline()
        self.bus.set_wake_callback(self._wake_teammate)

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("members"), list):
                    return data
            except json.JSONDecodeError:
                pass
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _mark_offline(self):
        changed = False
        for m in self.config.get("members", []):
            if m.get("status") in ("idle", "working"):
                m["status"] = "offline"
                changed = True
        if changed:
            self._save_config()

    def _find(self, name: str) -> dict | None:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def _set_status(self, name: str, status: str):
        with self.lock:
            m = self._find(name)
            if m:
                m["status"] = status
                self._save_config()

    def spawn(self, name: str, role: str, prompt: str) -> str:
        _NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

        name, role = name.strip(), role.strip() or "teammate"
        if not _NAME_RE.fullmatch(name):
            return "Error: 名字只能包含字母、数字、下划线、点和短横线，长度不超64"

        with self.lock:
            member = self._find(name)
            if member:
                running = self.threads.get(name)
                if running and running.is_alive():
                    self.bus.send("lead", name, prompt)
                    member["role"] = role
                    member["status"] = "working"
                    self._save_config()
                    return f"'{name}' 已在队中，任务已发送至 inbox"
                member["role"] = role
                member["status"] = "working"
            else:
                active = sum(1 for t in self.threads.values() if t.is_alive())
                if active >= _MAX_TEAMMATES:
                    return f"Error: 已达队友上限 {_MAX_TEAMMATES}，请先 shutdown 一些队友"
                member = {"name": name, "role": role, "status": "working"}
                self.config["members"].append(member)
            self._save_config()

        self._wake_events[name] = threading.Event()
        self._wake_events[name].set()
        logger.info(f"[spawn→] {name} role={role}")
        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()
        return f"已召入队友 '{name}'（职司：{role}）"

    def _wake_teammate(self, name: str):
        ev = self._wake_events.get(name)
        if ev:
            ev.set()

    def _teammate_loop(self, name: str, role: str, prompt: str):
        from .runner import run_agent
        from tools.team_tools import set_caller

        set_caller(name)

        system_prompt = (
            f"你是 agent team 中的固定队友，名叫 {name}，职司是 {role}。\n"
            f"工作区：{self.project_dir}。\n"
            "可用工具：run_command、web_fetch、load_skill、send_message。\n"
            "收到 inbox 任务后独立完成，完成后用 send_message 回禀 lead。\n"
            "重要：每轮任务结束后对话历史会重置，不要依赖上一轮的上下文。\n"
        )

        tool_names = list(_BASE_TOOL_NAMES) + ["send_message"]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        has_work = True
        ev = self._wake_events.get(name)
        if not ev:
            logger.error(f"[队友✗] {name} wake event 缺失，线程退出")
            self._set_status(name, "offline")
            return

        while True:
            ev.clear()
            inbox = self.bus.read_inbox(name)
            if inbox:
                logger.info(f"[队友←] {name} 收到 {len(inbox)} 条 inbox 消息")
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    self.bus.send(name, msg.get("from", "lead"),
                                  f"队友 {name} 已退出。", "shutdown_response")
                    self._set_status(name, "shutdown")
                    return
                messages.append({
                    "role": "user",
                    "content": "<inbox>\n" + json.dumps(msg, ensure_ascii=False, indent=2) + "\n</inbox>",
                })
                has_work = True

            if not has_work:
                ev.wait(timeout=TIMEOUTS["teammate_recv"])
                continue

            self._set_status(name, "working")
            logger.info(f"[队友▶] {name} 开始工作，消息数={len(messages)}")
            try:
                result = run_agent(messages, max_turns=TEAMMATE["max_turns"], tool_names=tool_names,
                                 context_length=MODEL_CONFIG.get("context_length", 128000))
            except Exception as exc:
                logger.error(f"[队友✗] {name} 异常: {exc}")
                self.bus.send(name, "lead", f"Error: 执行异常 {exc}")
                result = None

            if not result:
                self.bus.send(name, "lead",
                              f"队友 {name} 任务未完成（超出轮次或执行失败）")

            messages = [messages[0]]
            logger.info(f"[队友■] {name} 空闲，等待 inbox")
            self._set_status(name, "idle")
            has_work = False

    def list_all(self) -> str:
        with self.lock:
            if not self.config["members"]:
                return "暂无队友。"
            lines = [f"Team: {self.config.get('team_name', 'default')}"]
            for m in self.config["members"]:
                note = "（需重新 spawn 才会处理 inbox）" if m["status"] == "offline" else ""
                lines.append(f"  - {m['name']}（{m['role']}）：{m['status']}{note}")
            return "\n".join(lines)

    def member_names(self) -> list[str]:
        with self.lock:
            return [m["name"] for m in self.config["members"]]
