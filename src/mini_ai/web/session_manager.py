from __future__ import annotations
"""会话状态管理 — 封装全局 dict + 淘汰 + 组件创建 + CRUD

从 web/routes/chat.py 提取，统一管理 13 个全局 dict。
对外暴露 SessionManager 单例方法，不再暴露裸 dict。
"""
import threading
import uuid
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from ..core.runtime_types import MessageDict, MetadataDict, SessionComponents, TeamComponents, ToolDefinition, UsageDict
from .route_types import SessionMeta
from .session_cache import cleanup_components, evict_sessions
from .session_components import create_session_components
from .session_metadata import (
    build_meta,
    load_session_model,
    load_session_name,
    parse_created_at,
    save_session_model,
    save_session_name,
)
from .session_paths import get_workspace_session_base, resolve_session_base
from .session_persistence import load_messages_from_db
from .queue_utils import safe_queue_put
from ..logger import logger
from ..plan.schema import PlanSessionState
from ..utils import now_ts
from ..llm.base import rebuild_tool_messages as _rebuild_tool_messages


# ═══════════════════════════════════════════
# SessionState — 单个会话的所有状态
# ═══════════════════════════════════════════

@dataclass
class SessionState:
    """一个会话的完整状态（合并原 13 个 dict 中的对应字段）"""
    messages: list[MessageDict] = field(default_factory=list)
    model: str = ""
    status: str = "idle"
    access_time: float = 0.0
    last_usage: UsageDict = field(default_factory=dict)
    plan: PlanSessionState = field(default_factory=PlanSessionState)
    lock: threading.Lock = field(default_factory=threading.Lock)
    abort_event: threading.Event = field(default_factory=threading.Event)
    meta: MetadataDict = field(default_factory=dict)
    refs: int = 0
    components: SessionComponents = field(default_factory=SessionComponents)


# ═══════════════════════════════════════════
# SessionManager — 单例
# ═══════════════════════════════════════════

_MAX_CACHED_SESSIONS = 20

class SessionManager:
    """会话状态管理器

    封装原 13 个全局 dict，对外暴露方法而非裸 dict。
    其他路由通过 SessionManager.instance() 获取单例。
    """

    _instance: SessionManager | None = None

    @classmethod
    def instance(cls) -> SessionManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.RLock()
        # 核心状态：cache_key → SessionState
        self._sessions: dict[str, SessionState] = {}
        # 团队组件：ws_key → {bus, team_mgr, blackboard}
        self._team_components: dict[str, TeamComponents] = {}
        # 工具定义缓存
        self._lead_tools_cache: list[ToolDefinition] | None = None
        # 活跃生成任务：cache_key → task，用于跨 WebSocket 连接隔离同一会话生成
        self._active_tasks: dict[str, object] = {}

    # ── key 工具 ──

    @staticmethod
    def cache_key(username: str, workspace: str | None, sid: str) -> str:
        return f"{username}:{workspace or 'default'}:{sid}"

    @staticmethod
    def ws_key(username: str, workspace: str | None) -> str:
        return f"{username}:{workspace or 'default'}"

    # ── 状态读写（线程安全）──

    def get(self, key: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(key)

    def get_messages(self, key: str) -> list[MessageDict] | None:
        with self._lock:
            s = self._sessions.get(key)
            return s.messages if s else None

    def set_messages(self, key: str, messages: list[MessageDict]):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.messages = messages

    def set_status(self, key: str, status: str):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.status = status

    def get_status(self, key: str) -> str:
        with self._lock:
            s = self._sessions.get(key)
            return s.status if s else "idle"

    def set_model(self, key: str, model: str):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.model = model

    def get_model(self, key: str) -> str:
        with self._lock:
            s = self._sessions.get(key)
            return s.model if s else ""

    def set_plan_state(self, key: str, plan: PlanSessionState):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.plan = plan

    def get_plan_state(self, key: str) -> PlanSessionState:
        with self._lock:
            s = self._sessions.get(key)
            if s:
                return s.plan
            return PlanSessionState()

    def is_planning(self, key: str) -> bool:
        with self._lock:
            s = self._sessions.get(key)
            return bool(s and s.plan.state in ("planning", "awaiting_user", "awaiting_approval"))

    def set_plan_mode(self, key: str, mode: bool):
        """兼容旧调用；新代码应使用 set_plan_state。"""
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.plan.state = "planning" if mode else "idle"

    def get_plan_mode(self, key: str) -> bool:
        """兼容旧调用；新代码应使用 get_plan_state/is_planning。"""
        return self.is_planning(key)

    def set_last_usage(self, key: str, usage: UsageDict):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.last_usage = usage

    def get_abort_event(self, key: str) -> threading.Event | None:
        with self._lock:
            s = self._sessions.get(key)
            return s.abort_event if s else None

    def get_lock(self, key: str) -> threading.Lock:
        with self._lock:
            s = self._sessions.get(key)
            if s:
                return s.lock
            # 不存在则创建
            s = SessionState()
            self._sessions[key] = s
            return s.lock

    def get_components(self, key: str) -> SessionComponents | None:
        with self._lock:
            s = self._sessions.get(key)
            return s.components if s else None

    def claim_active_task(self, key: str, task: object) -> bool:
        """尝试登记活跃生成任务；同一会话已有未完成任务时返回 False。"""
        with self._lock:
            existing = self._active_tasks.get(key)
            if existing and not existing.done():
                return False
            self._active_tasks[key] = task
            return True

    def release_active_task(self, key: str, task: object | None = None):
        """释放活跃生成任务；传入 task 时只释放当前登记的任务。"""
        with self._lock:
            if task is not None and self._active_tasks.get(key) is not task:
                return
            self._active_tasks.pop(key, None)

    def set_components(self, key: str, components: SessionComponents):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.components = components

    def get_refs(self, key: str) -> int:
        with self._lock:
            s = self._sessions.get(key)
            return s.refs if s else 0

    def get_meta(self, key: str) -> SessionMeta | None:
        with self._lock:
            s = self._sessions.get(key)
            return s.meta if s else None

    def set_meta(self, key: str, meta: SessionMeta):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.meta = meta

    # ── 引用计数 ──

    def inc_ref(self, key: str):
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.refs += 1

    def dec_ref(self, key: str):
        with self._lock:
            s = self._sessions.get(key)
            if s and s.refs > 0:
                s.refs -= 1

    # ── 淘汰 ──

    def touch(self, key: str):
        """更新访问时间，必要时淘汰"""
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.access_time = time.monotonic()
            if len(self._sessions) > _MAX_CACHED_SESSIONS:
                self._evict(keep_key=key)

    def _evict(self, keep_key: str | None = None):
        """淘汰最老的非活跃会话（在 _lock 内调用）"""
        evicted = evict_sessions(self._sessions, max_cached_sessions=_MAX_CACHED_SESSIONS, keep_key=keep_key)
        if evicted > 0:
            logger.info(f"[Web] 淘汰 {evicted} 个非活跃会话缓存，当前缓存数: {len(self._sessions)}")

    # ── CRUD ──

    def create_session(self, key: str, messages: list[MessageDict]):
        """创建新会话"""
        with self._lock:
            self._sessions[key] = SessionState(messages=messages, access_time=time.monotonic())

    def delete_session(self, key: str):
        """删除会话"""
        with self._lock:
            s = self._sessions.pop(key, None)
            if s:
                cleanup_components(s.components)

    def reset_session(self, key: str, system_content: str, old_name: str = ""):
        """重置会话（保留 system prompt）"""
        with self._lock:
            s = self._sessions.get(key)
            if s:
                s.messages = [{"role": "system", "content": system_content, "name": old_name or "新会话", "timestamp": now_ts()}]
                s.plan = PlanSessionState()
                s.components = {}
                s.refs = 0

    # ── abort ──

    def abort_all(self):
        """设置所有活跃会话的 abort_event"""
        with self._lock:
            for s in self._sessions.values():
                s.abort_event.set()
        logger.info(f"[Web] abort_all_sessions: 已中止 {len(self._sessions)} 个会话")

    def clear_workspace_prefix(self, prefix: str):
        """清除指定工作空间前缀的所有缓存会话"""
        with self._lock:
            keys_to_delete = [k for k in self._sessions if k.startswith(prefix)]
            for k in keys_to_delete:
                s = self._sessions.pop(k)
                cleanup_components(s.components)

        # ── 团队组件 ──

    def get_team_component(self, ws_key: str) -> TeamComponents | None:
        with self._lock:
            return self._team_components.get(ws_key)

    def set_team_component(self, ws_key: str, comp: TeamComponents):
        with self._lock:
            self._team_components[ws_key] = comp

    def has_team_component(self, ws_key: str) -> bool:
        with self._lock:
            return ws_key in self._team_components

    # ── 工具定义缓存 ──

    def lead_tool_defs(self) -> list[ToolDefinition]:
        raise RuntimeError("SessionManager.lead_tool_defs is compatibility-only; use SessionRuntimeContext.tool_registry")

    def invalidate_lead_tools(self):
        self._lead_tools_cache = None


# ═══════════════════════════════════════════
# 向后兼容的模块级函数（供其他路由 import）
# ═══════════════════════════════════════════

def cache_key(username: str, workspace: str | None, sid: str) -> str:
    return SessionManager.cache_key(username, workspace, sid)

def ws_key(username: str, workspace: str | None) -> str:
    return SessionManager.ws_key(username, workspace)

def abort_all_sessions():
    SessionManager.instance().abort_all()

def lead_tool_defs() -> list[ToolDefinition]:
    raise RuntimeError("lead_tool_defs is compatibility-only; use SessionRuntimeContext.tool_registry")

def invalidate_lead_tools():
    SessionManager.instance().invalidate_lead_tools()


# ═══════════════════════════════════════════
# 路径解析
# ═══════════════════════════════════════════

def resolve_base(username: str, workspace: str | None) -> Path:
    """解析工作空间下的 sessions 目录"""
    return resolve_session_base(username, workspace)


def _get_workspace_base(username: str, workspace: str | None) -> Path | None:
    return get_workspace_session_base(username, workspace)


# ═══════════════════════════════════════════
# 组件创建 + system prompt 构建
# ═══════════════════════════════════════════

def get_or_create_components(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> SessionComponents:
    sm = SessionManager.instance()
    key = sm.cache_key(username, workspace, sid)

    with sm._lock:
        s = sm._sessions.get(key)
        if s and s.components:
            comp = s.components
            wk = sm.ws_key(username, workspace)
            team_comp = sm._team_components.get(wk)
            if team_comp and not comp.get("bus"):
                comp["bus"] = team_comp.get("bus")
                comp["team_mgr"] = team_comp.get("team_mgr")
                comp["blackboard"] = team_comp.get("blackboard")
            return comp

        components = _create_components_locked(username, sid, base, workspace, key)
        if s:
            s.components = components
        else:
            sm._sessions[key] = SessionState(components=components, access_time=time.monotonic())
        return components

def _create_components_locked(username: str, sid: str, base: Path | None, workspace: str | None, cache_key: str) -> SessionComponents:
    sm = SessionManager.instance()
    return create_session_components(
        username,
        sid,
        base,
        workspace,
        resolve_base=resolve_base,
        ws_key=sm.ws_key,
        get_team_component=sm.get_team_component,
        set_team_component=sm.set_team_component,
        has_team_component=sm.has_team_component,
    )

def build_system_prompt(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> str:
    _t0 = time.time()
    if base is None:
        base = resolve_base(username, workspace or "default")
    comp = get_or_create_components(username, sid, base, workspace)
    result = comp["ctx_builder"].build(
        memory_store=comp["store"],
        skill_loader=comp["skill_loader"],
        project_path=comp["project_path"],
    )
    logger.debug(f"[perf] build_system_prompt sid={sid} len={len(result)} time={time.time()-_t0:.3f}s")
    return result


# ═══════════════════════════════════════════
# 会话加载/创建
# ═══════════════════════════════════════════

def _load_from_db(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> list[MessageDict] | None:
    return load_messages_from_db(
        username,
        sid,
        base,
        workspace,
        resolve_base=resolve_base,
        get_components=get_or_create_components,
    )

def _parse_created_at(sid: str) -> str:
    return parse_created_at(sid)


def _load_session_name(base: Path | None, sid: str) -> str:
    return load_session_name(base, sid)


def _save_session_name(base: Path | None, sid: str, name: str):
    save_session_name(base, sid, name)


def _load_session_model(base: Path | None, sid: str) -> str:
    return load_session_model(base, sid)

def _restore_session_model(base: Path | None, sid: str, cache_key: str):
    model = _load_session_model(base, sid)
    if model:
        SessionManager.instance().set_model(cache_key, model)

def _build_meta(sid: str, messages: list[MessageDict], username: str, workspace: str | None = None) -> SessionMeta:
    sm = SessionManager.instance()
    return build_meta(
        sid,
        messages,
        username,
        workspace,
        resolve_base=resolve_base,
        get_model=sm.get_model,
        get_status=sm.get_status,
        cache_key=sm.cache_key,
    )

def _update_meta_cache(username: str, sid: str, workspace: str | None = None, messages: list[MessageDict] | None = None):
    sm = SessionManager.instance()
    key = sm.cache_key(username, workspace, sid)
    if messages is not None:
        sm.set_meta(key, _build_meta(sid, messages, username, workspace))

def _save_session_model(base: Path | None, sid: str, model_name: str):
    save_session_model(base, sid, model_name)

def get_or_create_session(username: str, session_id: str | None, base: Path | None = None, workspace: str | None = None, *, create: bool = True) -> tuple[str, list[MessageDict] | None]:
    _t0 = time.time()
    sm = SessionManager.instance()

    with sm._lock:
        if not session_id:
            if not create:
                return "", None
            session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        sid = session_id
        key = sm.cache_key(username, workspace, sid)

        # 检查内存缓存
        s = sm._sessions.get(key)
        if s and s.messages:
            _restore_session_model(base, sid, key)
            s.refs += 1
            s.access_time = time.monotonic()
            return sid, s.messages

        # 尝试从数据库加载
        loaded = _load_from_db(username, sid, base, workspace)
        if loaded and len(loaded) >= 1:
            if loaded[0].get("role") != "system":
                prompt = build_system_prompt(username, sid, base, workspace)
                saved_name = _load_session_name(base, sid) or "新会话"
                loaded.insert(0, {"role": "system", "content": prompt, "name": saved_name, "timestamp": now_ts()})
            loaded = _rebuild_tool_messages(loaded)
            sm._sessions[key] = SessionState(messages=loaded, access_time=time.monotonic(), refs=1)
            _update_meta_cache(username, sid, workspace, loaded)
            _restore_session_model(base, sid, key)
            return sid, loaded

        if not create:
            return sid, None

        # 全新会话
        prompt = build_system_prompt(username, sid, base, workspace)
        msgs = [{"role": "system", "content": prompt, "name": "新会话", "timestamp": now_ts()}]
        sm._sessions[key] = SessionState(messages=msgs, access_time=time.monotonic(), refs=1)
        _update_meta_cache(username, sid, workspace, msgs)
        logger.debug(f"[perf] get_or_create_session sid={sid} ws={workspace} create={create} time={time.time()-_t0:.3f}s")
        return sid, msgs
