#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持久队友管理：spawn、状态追踪、线程循环"""
import json
import re
import threading
import time
from pathlib import Path

from ..config import DATA_DIR
from ..core.settings import ModelSettings, TeamSettings, TimeoutSettings
from ..logger import logger
from .prompts import build_team_prompt
from ..utils import now_ts
from ..core.runtime_types import ACTIVE_TEAM_MEMBER_STATUSES
from .models import TeamConfigDict, TeamListText, TeamMemberStatus, TeamMemberSummary, team_member_summary

_DEFAULT_TEAM_SETTINGS = TeamSettings()
_DEFAULT_TIMEOUT_SETTINGS = TimeoutSettings()

class TeammateManager:
    """队友持久化管理，配置持久到 team_config.json"""

    def __init__(self, *, team_dir: Path, bus, project_dir: Path, team_settings: TeamSettings | None = None, timeout_settings: TimeoutSettings | None = None):
        self.dir = Path(team_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "team_config.json"
        self.bus = bus
        self.project_dir = project_dir
        self.team_settings = team_settings or _DEFAULT_TEAM_SETTINGS
        self.timeout_settings = timeout_settings or _DEFAULT_TIMEOUT_SETTINGS
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}
        self._wake_events: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self._display = None
        self._mark_offline()
        self.bus.set_wake_callback(self._wake_teammate)

    def set_display(self, display):
        self._display = display

    def _load_config(self) -> TeamConfigDict:
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("members"), list):
                    return {
                        "team_name": str(data.get("team_name") or "default"),
                        "members": [
                            team_member_summary(m.get("name", ""), m.get("role", ""), m.get("status", "offline"))
                            for m in data.get("members", [])
                            if isinstance(m, dict)
                        ],
                    }
            except json.JSONDecodeError:
                pass
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _mark_offline(self):
        with self.lock:
            changed = False
            for m in self.config.get("members", []):
                if m.get("status") in ACTIVE_TEAM_MEMBER_STATUSES:
                    m["status"] = "offline"
                    changed = True
            if changed:
                self._save_config()

    def _find(self, name: str) -> TeamMemberSummary | None:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def active_member_names(self) -> list[str]:
        with self.lock:
            return [
                member["name"]
                for member in self.config.get("members", [])
                if member["status"] in ACTIVE_TEAM_MEMBER_STATUSES
            ]

    def has_working_members(self) -> bool:
        with self.lock:
            return any(member["status"] == "working" for member in self.config.get("members", []))

    def is_member_active(self, name: str) -> bool:
        thread = self.threads.get(name)
        return bool(self._find(name) and thread and thread.is_alive())

    def workspace_skills_dir(self) -> Path | None:
        return self.project_dir / "skills"

    def _set_status(self, name: str, status: TeamMemberStatus):
        with self.lock:
            m = self._find(name)
            if m:
                m["status"] = status
                self._save_config()
        if self._display:
            try:
                self._display.teammate_status(name, status)
            except Exception:
                pass

    def spawn(self, name: str, role: str, prompt: str, *, derived_agent_resources=None) -> str:
        _NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

        name, role = name.strip(), role.strip() or "teammate"
        if not _NAME_RE.fullmatch(name):
            return "Error: 名字只能包含字母、数字、下划线、点和短横线，长度不超64"

        settings = getattr(derived_agent_resources, "settings", None)
        effective_team_settings = settings.team if settings else self.team_settings

        with self.lock:
            member = self._find(name)
            if member:
                running = self.threads.get(name)
                if running and running.is_alive():
                    self.bus.send("lead", name, prompt)
                    member["role"] = role
                    member["status"] = "working"
                    self._save_config()
                    return f"'{name}' 已在队中，新任务已发送至 inbox。等待它完成后自动通知你即可。"
                member["role"] = role
                member["status"] = "working"
            else:
                active = sum(1 for t in self.threads.values() if t.is_alive())
                max_teammates = effective_team_settings.max_teammates
                if active >= max_teammates:
                    return f"Error: 已达队友上限 {max_teammates}，请先 shutdown 一些队友"
                member = team_member_summary(name, role, "working")
                self.config["members"].append(member)
            self._save_config()

            self._wake_events[name] = threading.Event()
            self._wake_events[name].set()

            # P0#5: 线程创建和注册在同一个 lock 块内，避免竞态窗口
            import contextvars as _cv
            parent_ctx = _cv.copy_context()
            thread = threading.Thread(
                target=parent_ctx.run,
                args=(self._teammate_loop, name, role, prompt, self._display, derived_agent_resources),
                daemon=True,
            )
            self.threads[name] = thread

        logger.info(f"[spawn→] {name} role={role}")
        thread.start()
        return f"已召入队友 '{name}'（职司：{role}），它将独立完成任务并回禀你。你不需要再做相同的分析工作，等待它完成后自动通知你即可。"

    def _wake_teammate(self, name: str):
        ev = self._wake_events.get(name)
        if ev:
            ev.set()

    def _teammate_loop(self, name: str, role: str, prompt: str, lead_display=None, derived_agent_resources=None):
        from ..runner import run_agent
        from ..tools.team_tools import set_caller

        set_caller(name)
        

        settings = getattr(derived_agent_resources, "settings", None)
        team_settings = settings.team if settings else self.team_settings
        timeout_settings = settings.timeouts if settings else self.timeout_settings
        model_settings = settings.model if settings else ModelSettings.from_dict(None)
        model_config = model_settings.to_dict()
        tm_display = None
        ctx = None
        if lead_display:
            try:
                tm_display = lead_display.child(teammate=name)
                tm_display.agent_start(
                    agent_type=name,
                    role=role,
                    task=prompt[:100] + "..." if len(prompt) > 100 else prompt,
                )
            except Exception as exc:
                logger.debug(f"[队友] 创建 display 失败: {exc}")
                tm_display = None
            from ..config import RequestContext
            ctx = RequestContext(model_config=model_config, display=tm_display)

        from ..context import ContextBuilder
        from ..skills import SkillLoader
        from ..config import DATA_DIR, PACKAGE_DIR, SKILL_PATHS as _SP

        ctx_builder = getattr(derived_agent_resources, "context_builder", None) or ContextBuilder(DATA_DIR)
        _sl = getattr(derived_agent_resources, "skill_loader", None) or SkillLoader(DATA_DIR / "skills", _SP)
        base_prompt = ctx_builder.build(skill_loader=_sl, project_path=str(self.project_dir), exclude_character=True)
        tool_names = list(team_settings.base_tools) + [
            "send_message", "list_teammates",
            "blackboard_read", "blackboard_write", "blackboard_list",
            "dispatch_subagent",
        ]
        team_rules = build_team_prompt(
            f"你是 agent team 中的队友，名叫 {name}，职司 {role}。",
            tool_names,
            has_messaging=True,
        )
        system_prompt = team_rules + "\n\n---\n\n" + base_prompt if base_prompt else team_rules

        _ts = now_ts()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt, "timestamp": _ts},
        ]
        has_work = True
        ev = self._wake_events.get(name)
        if not ev:
            logger.error(f"[队友✗] {name} wake event 缺失，线程退出")
            self._set_status(name, "offline")
            # 手动清理资源（因为还没进入 try-except-finally）
            with self.lock:
                self.threads.pop(name, None)
                self._wake_events.pop(name, None)
            return

        try:
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
                    "timestamp": now_ts(),
                })
                has_work = True

            if not has_work:
                ev.wait(timeout=timeout_settings.teammate_recv)
                continue

            self._set_status(name, "working")
            logger.info(f"[队友▶] {name} 开始工作，消息数={len(messages)}")
            try:
                tool_registry = getattr(derived_agent_resources, "tool_registry", None)
                if tool_registry is None:
                    result = "⚠ 队友执行失败: 缺少 session-local runtime resources"
                else:
                    context_length = model_settings.context_length
                    result = run_agent(
                        messages,
                        max_turns=team_settings.max_turns,
                        tool_names=tool_names,
                        context_length=context_length,
                        ctx=ctx,
                        abort_event=getattr(derived_agent_resources, "abort_event", None),
                        compactor=getattr(derived_agent_resources, "compactor", None),
                        tool_registry=tool_registry,
                    )
            except Exception as exc:
                logger.error(f"[队友✗] {name} 异常: {exc}", exc_info=True)
                # 异常信息通过 bus 发送给 lead，不会吞掉
                self.bus.send(name, "lead", f"⚠ 队友 {name} 执行异常: {type(exc).__name__}: {exc}")
                result = None

            if not result:
                self.bus.send(name, "lead",
                              f"队友 {name} 任务未完成（超出轮次或执行失败）")

            max_history = team_settings.max_history
            if len(messages) > max_history:
                messages[:] = [messages[0]] + messages[-(max_history - 1):]
            logger.info(f"[队友■] {name} 空闲，等待 inbox")
            self._set_status(name, "idle")
            has_work = False
            idle_start = time.monotonic()

            while not has_work:
                ev.clear()
                ev.wait(timeout=timeout_settings.teammate_recv)
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self.bus.send(name, msg.get("from", "lead"),
                                          f"队友 {name} 已退出。", "shutdown_response")
                            self._set_status(name, "shutdown")
                            return
                        messages.append({
                            "role": "user",
                            "content": "<inbox>\n" + json.dumps(msg, ensure_ascii=False, indent=2) + "\n</inbox>",
                            "timestamp": now_ts(),
                        })
                        has_work = True
                    if has_work:
                        break
                idle_timeout = team_settings.idle_timeout
                if idle_timeout > 0 and (time.monotonic() - idle_start) > idle_timeout:
                    logger.info(f"[队友⏱] {name} 空闲超时 ({idle_timeout}s)，自动退出")
                    self._set_status(name, "shutdown")
                    return
        except Exception as exc:
            logger.error(f"[队友✗] {name} 未预期异常，线程退出: {exc}", exc_info=True)
            self._set_status(name, "offline")
        finally:
            # 清理线程资源
            with self.lock:
                self.threads.pop(name, None)
                self._wake_events.pop(name, None)
            logger.info(f"[队友 cleanup] {name} 线程资源已清理")

    def list_all(self) -> TeamListText:
        with self.lock:
            if not self.config["members"]:
                return "暂无队友。"
            lines = [f"Team: {self.config.get('team_name', 'default')}"]
            for m in self.config["members"]:
                note = "（需重新 spawn 才会处理 inbox）" if m["status"] == "offline" else ""
                lines.append(f"  - {m['name']}（{m['role']}）：{m['status']}{note}")
            return "\n".join(lines)

    def member_summaries(self) -> list[TeamMemberSummary]:
        with self.lock:
            return [team_member_summary(m["name"], m["role"], m["status"]) for m in self.config["members"]]

    def member_names(self) -> list[str]:
        with self.lock:
            return [m["name"] for m in self.config["members"]]
