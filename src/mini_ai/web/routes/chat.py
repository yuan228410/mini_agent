"""聊天接口 — WebSocket 模式，多用户，HistoryDB 持久化，多会话并行"""
import asyncio
import json
import threading
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ...config import DATA_DIR, MODEL_CONFIG, STREAMING, COMPACTOR, WEB, PLAN, RequestContext, get_model_config, user_data_dir
from ...llm import get_usage, reset_usage, chat as llm_chat
from ...runner import run_tool_loop
from ...tools import get_definitions, register_memory_tools, register_history_tools, register, inject_todos as _inject_todos
from ...logger import logger
from ...tools import register_team, register_blackboard
from ...config import PACKAGE_DIR
from ..display import WebDisplay
from ...utils import now_ts

router = APIRouter()

_sessions_lock = threading.RLock()

# 线程池配置（限制并发数）
_MAX_CONCURRENT_SESSIONS = 10  # 最大并发会话数
_executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_SESSIONS, thread_name_prefix="chat-")
_concurrent_semaphore = threading.Semaphore(_MAX_CONCURRENT_SESSIONS)

def _safe_queue_put(queue, item):
    """Queue.put_nowait 的安全版本，QueueFull 时静默丢弃（前端 watchdog 兜底）"""
    try:
        queue.put_nowait(item)
    except Exception:
        pass


def abort_all_sessions():
    """设置所有活跃会话的 abort_event，让 run_tool_loop 尽快退出"""
    with _sessions_lock:
        for key, evt in _SESSION_ABORTS.items():
            evt.set()
    logger.info(f"[Web] abort_all_sessions: 已中止 {len(_SESSION_ABORTS)} 个会话")

# 三层缓存 key = f"{username}:{workspace}:{sid}"
# 所有缓存 dict 统一使用此 key 格式，与存储路径对齐

def _cache_key(username: str, workspace: str | None, sid: str) -> str:
    return f"{username}:{workspace or 'default'}:{sid}"

# 扁平结构，统一 key 格式
_SESSIONS: dict[str, list[dict]] = {}
_SESSION_MODELS: dict[str, str] = {}
_SESSION_ACCESS: dict[str, float] = {}  # key → last access timestamp
_LAST_USAGE: dict[str, dict] = {}
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_STATUS: dict[str, str] = {}
_SESSION_ABORTS: dict[str, threading.Event] = {}
_META_CACHE: dict[str, dict] = {}
_SESSION_COMPONENTS: dict[str, dict] = {}
_SESSION_PLAN_MODE: dict[str, bool] = {}
_TEAM_COMPONENTS: dict[str, dict] = {}
_SESSION_REFS: dict[str, int] = {}  # 引用计数，防止淘汰正在使用的会话

_MAX_CACHED_SESSIONS = 20  # 降低缓存上限，减少资源占用

def _cleanup_session_components(comp: dict):
    """清理会话组件资源"""
    if not comp:
        return
    resources = ["compactor", "store"]  # history_db 现在是统一连接池，不单独关闭
    for name in resources:
        try:
            obj = comp.get(name)
            if obj and hasattr(obj, "close"):
                obj.close()
                logger.debug(f"[Web] 已关闭资源: {name}")
        except Exception as e:
            logger.warning(f"[Web] 关闭 {name} 失败: {e}")

def _touch_session(cache_key: str):
    """更新会话访问时间，必要时淘汰最老的会话"""
    with _sessions_lock:  # 整个淘汰操作加锁，避免并发修改
        _SESSION_ACCESS[cache_key] = time.monotonic()
        if len(_SESSION_ACCESS) > _MAX_CACHED_SESSIONS:
            # 淘汰最老的 5 个会话（跳过正在使用的）
            candidates = [(k, _SESSION_ACCESS.get(k, 0)) for k in list(_SESSION_ACCESS.keys())]
            candidates.sort(key=lambda x: x[1])  # 按访问时间排序
            
            evicted = 0
            for k, _ in candidates:
                if evicted >= len(_SESSION_ACCESS) - _MAX_CACHED_SESSIONS + 5:
                    break
                # P2#11: 跳过当前会话，防止淘汰刚创建的会话
                if k == cache_key:
                    continue
                # 跳过正在使用的会话（引用计数 > 0）
                if _SESSION_REFS.get(k, 0) > 0:
                    continue
                # 跳过正在生成的会话
                if _SESSION_STATUS.get(k) == "generating":
                    continue
                    
                comp = _SESSION_COMPONENTS.pop(k, None)
                if comp:
                    _cleanup_session_components(comp)
                _SESSIONS.pop(k, None)
                _META_CACHE.pop(k, None)
                _SESSION_STATUS.pop(k, None)
                _SESSION_MODELS.pop(k, None)
                _SESSION_LOCKS.pop(k, None)
                _SESSION_ACCESS.pop(k, None)
                _SESSION_REFS.pop(k, None)
                _SESSION_ABORTS.pop(k, None)  # 清理 abort event
                evicted += 1
            
            if evicted > 0:
                logger.info(f"[Web] 淘汰 {evicted} 个非活跃会话缓存，当前缓存数: {len(_SESSION_ACCESS)}")

def _ws_key(username: str, workspace: str | None) -> str:
    return f"{username}:{workspace or 'default'}"
# 注意：_SESSION_WORKSPACE 已删除，workspace 已编码在 key 中

def _get_workspace_base(username: str, workspace: str | None) -> Path | None:
    if not workspace:
        return None
    from ...workspace import WorkspaceManager
    ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
    ws = ws_mgr.get(workspace)
    if ws:
        base = ws.ws_dir / "sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return None

def _resolve_base(username: str, workspace: str | None) -> Path:
    """解析工作空间下的 sessions 目录。"""
    if not workspace:
        workspace = "default"
    ws_base = _get_workspace_base(username, workspace)
    if ws_base:
        return ws_base
    from ...workspace import WorkspaceManager
    ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
    ws = ws_mgr.get(workspace)
    if ws:
        base = ws.ws_dir / "sessions"
        base.mkdir(parents=True, exist_ok=True)
        return base
    # 仅 default 不存在时自动创建
    if workspace == "default":
        ws_mgr.create("default", str(Path.cwd()))
        ws = ws_mgr.get("default")
        if ws:
            base = ws.ws_dir / "sessions"
            base.mkdir(parents=True, exist_ok=True)
            return base
    raise ValueError(f"工作空间 '{workspace}' 不存在")

def _get_session_lock(username: str, workspace: str | None, sid: str) -> threading.Lock:
    key = _cache_key(username, workspace, sid)
    with _sessions_lock:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = threading.Lock()
        return _SESSION_LOCKS[key]

_WEB_LEAD_TOOLS: list[dict] | None = None

def _lead_tool_defs() -> list[dict]:
    global _WEB_LEAD_TOOLS
    if _WEB_LEAD_TOOLS is None:
        _WEB_LEAD_TOOLS = [d for d in get_definitions() if d["function"]["name"] not in ("read_inbox", "list_teammates")]
    return _WEB_LEAD_TOOLS

def _invalidate_lead_tools():
    """工具注册变更时清空缓存（如 MCP 加载、子代理注册）"""
    global _WEB_LEAD_TOOLS
    _WEB_LEAD_TOOLS = None

def _get_or_create_components(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> dict:
    cache_key = _cache_key(username, workspace, sid)
    
    # 双重检查锁定：第一次检查（快速路径）
    with _sessions_lock:
        if cache_key in _SESSION_COMPONENTS:
            comp = _SESSION_COMPONENTS[cache_key]
            wk = _ws_key(username, workspace)
            team_comp = _TEAM_COMPONENTS.get(wk, {})
            if team_comp and not comp.get("bus"):
                comp["bus"] = team_comp.get("bus")
                comp["team_mgr"] = team_comp.get("team_mgr")
                comp["blackboard"] = team_comp.get("blackboard")
            return comp
        
        # 锁内创建组件，避免竞态条件
        components = _create_components_locked(username, sid, base, workspace, cache_key)
        _SESSION_COMPONENTS[cache_key] = components
        return components

def _create_components_locked(username: str, sid: str, base: Path | None, workspace: str | None, cache_key: str) -> dict:
    """在锁内创建会话组件，避免竞态条件"""
    from ...memory import MemoryStore, Compactor, HistoryDBPool
    from ...context import ContextBuilder
    from ...skills import SkillLoader
    from ..deps import SKILL_PATHS

    project_path = ""
    ws_dir = None
    if workspace:
        from ...workspace import WorkspaceManager
        ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
        ws = ws_mgr.get(workspace)
        if ws:
            project_path = ws.project_path
            ws_dir = ws.ws_dir
        else:
            logger.warning(f"[Web] 工作空间 '{workspace}' 不存在，使用默认配置")

    user_memory_dir = user_data_dir(username) / "memory"

    if base is None:
        base = _resolve_base(username, workspace or "default")
    session_dir = base / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    session_memory_dir = session_dir / "memory_data"
    session_memory_dir.mkdir(parents=True, exist_ok=True)

    global_memory_dir = DATA_DIR / "memory"
    ws_memory_dir = ws_dir / "memory_data" if ws_dir else None
    user_store = MemoryStore(user_memory_dir, episode_dir=session_memory_dir,
                             global_memory_dir=global_memory_dir,
                             workspace_memory_dir=ws_memory_dir)

    # 使用统一数据库连接池
    history_db = HistoryDBPool.get(username)

    user_skills_dir = user_data_dir(username) / "skills"
    ws_skills_dir = ws_dir / "skills" if ws_dir else None
    skill_loader = SkillLoader(DATA_DIR / "skills", SKILL_PATHS, user_skills_dir=user_skills_dir, workspace_skills_dir=ws_skills_dir)

    ctx_builder = ContextBuilder(DATA_DIR)
    
    compactor = Compactor(
        user_store,
        keep_recent=COMPACTOR.get("keep_recent", 50),
        context_usage_threshold=COMPACTOR.get("context_usage_threshold", 0.8),
        keep_budget_ratio=COMPACTOR.get("keep_budget_ratio", 0.2),
        early_compact_ratio=COMPACTOR.get("early_compact_ratio", 0.85),
        max_cached_summaries=COMPACTOR.get("max_cached_summaries", 200),
        max_summary_sections=COMPACTOR.get("max_summary_sections", 50),
        context_length=MODEL_CONFIG.get("context_length", 256000),
        context_builder=ctx_builder,
        skill_loader=skill_loader,
        project_path=project_path,
        summary_dir=session_dir,
    )

    components = {
        "store": user_store,
        "history_db": history_db,
        "compactor": compactor,
        "ctx_builder": ctx_builder,
        "project_path": project_path,
        "skill_loader": skill_loader,
    }
    
    # 创建团队组件（也在锁内）
    wk = _ws_key(username, workspace)
    if ws_dir:
        # 再次检查，因为可能在等待锁期间被其他线程创建
        if wk not in _TEAM_COMPONENTS:
            from ...team import MessageBus, TeammateManager, Blackboard
            team_dir = ws_dir / ".team"
            bus = MessageBus(team_dir / "inbox")
            team_mgr = TeammateManager(team_dir=team_dir, bus=bus, project_dir=ws_dir)
            bb = Blackboard(persist_path=team_dir / "blackboard.json")
            _TEAM_COMPONENTS[wk] = {"bus": bus, "team_mgr": team_mgr, "blackboard": bb}
    
    team_comp = _TEAM_COMPONENTS.get(wk, {})
    components["bus"] = team_comp.get("bus")
    components["team_mgr"] = team_comp.get("team_mgr")
    components["blackboard"] = team_comp.get("blackboard")
    
    return components


def _build_system_prompt(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> str:
    _t0 = time.time()
    if base is None:
        base = _resolve_base(username, workspace or "default")
    comp = _get_or_create_components(username, sid, base, workspace)
    result = comp["ctx_builder"].build(
        memory_store=comp["store"],
        skill_loader=comp["skill_loader"],
        project_path=comp["project_path"],
    )
    logger.debug(f"[perf] _build_system_prompt sid={sid} len={len(result)} time={time.time()-_t0:.3f}s")
    return result

def _parse_created_at(sid: str) -> str:
    try:
        dt = datetime.strptime(sid[:15], "%Y%m%d-%H%M%S")
        return dt.isoformat()
    except (ValueError, IndexError):
        return ""

def _build_meta(sid: str, messages: list[dict], username: str, workspace: str | None = None) -> dict:
    cache_key = _cache_key(username, workspace, sid)
    non_system = [m for m in messages if m["role"] != "system"]
    first_user = next((m.get("content", "")[:50] for m in non_system if m["role"] == "user"), "")
    # meta.json 是名称的 source of truth，始终优先读取
    name = ""
    try:
        base = _resolve_base(username, workspace or "default")
        name = _load_session_name(base, sid)
    except Exception:
        pass
    # meta.json 无记录时，从消息中取
    if not name:
        name = messages[0].get("name", "") if messages else ""
    if not name:
        name = first_user or "新会话"
    return {
        "session_id": sid,
        "name": name,
        "model": _load_session_model(base, sid) or "",
        "message_count": len(non_system),
        "preview": first_user,
        "created_at": _parse_created_at(sid),
        "updated_at": next((m.get("timestamp", "") for m in reversed(non_system) if m.get("timestamp")), _parse_created_at(sid)),
        "status": _SESSION_STATUS.get(cache_key, "idle"),
    }

def _update_meta_cache(username: str, sid: str, workspace: str | None = None, messages: list[dict] | None = None):
    cache_key = _cache_key(username, workspace, sid)
    if messages is not None:
        _META_CACHE[cache_key] = _build_meta(sid, messages, username, workspace)
    else:
        _META_CACHE.pop(cache_key, None)

def _save_session_name(base: Path | None, sid: str, name: str):
    if not base:
        return
    meta_path = base / sid / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta["name"] = name
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

def _load_session_name(base: Path | None, sid: str) -> str:
    if not base:
        return ""
    meta_path = base / sid / "meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("name", "")
        except Exception:
            pass
    return ""

def _save_session_model(base: Path | None, sid: str, model_name: str):
    if not base:
        return
    meta_path = base / sid / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta["model"] = model_name
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

def _load_session_model(base: Path | None, sid: str) -> str:
    if not base:
        return ""
    meta_path = base / sid / "meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("model", "")
        except Exception:
            pass
    return ""

def _restore_session_model(base: Path | None, sid: str, cache_key: str):
    """从 meta.json 恢复模型选择到内存"""
    model = _load_session_model(base, sid)
    if model:
        _SESSION_MODELS[cache_key] = model

_MAX_HISTORY_LOAD = 2000

def _ensure_user_sessions(username: str) -> dict[str, list[dict]]:
    # 兼容旧接口——_SESSIONS 已扁平化，此函数保持返回 dict 供外部，但不再使用
    return {}

def _load_from_db(username: str, sid: str, base: Path | None = None, workspace: str | None = None) -> list[dict] | None:
    _t0 = time.time()
    try:
        if base is None:
            base = _resolve_base(username, workspace or "default")
        comp = _get_or_create_components(username, sid, base, workspace)
        result = comp["history_db"].load_session(workspace or "default", sid, limit=COMPACTOR.get("context_limit", 50))
        logger.debug(f"[perf] _load_from_db sid={sid} msgs={len(result) if result else 0} time={time.time()-_t0:.3f}s")
        return result
    except Exception as e:
        logger.error(f"[Web] _load_from_db error: {e}", exc_info=True)
        return None

def _rebuild_tool_messages(messages: list[dict]) -> list[dict]:
    has_tool_msgs = any(m.get("role") == "tool" for m in messages)
    result = []
    for m in messages:
        result.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tc_result = tc.get("_result", "")
                if has_tool_msgs and not tc_result:
                    continue
                tool_msg = {"role": "tool", "content": tc_result, "name": tc.get("function", {}).get("name", "")}
                if tc.get("id"):
                    tool_msg["tool_call_id"] = tc["id"]
                result.append(tool_msg)
    return result

def _get_or_create_session(username: str, session_id: str | None, base: Path | None = None, workspace: str | None = None, *, create: bool = True) -> tuple[str, list[dict] | None]:
    _t0 = time.time()
    with _sessions_lock:
        if not session_id:
            if not create:
                logger.warning(f"[session] create=False 但无 session_id，返回 None")
                return "", None
            session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        sid = session_id
        cache_key = _cache_key(username, workspace, sid)

        # 检查内存缓存
        if cache_key in _SESSIONS:
            existing = _SESSIONS[cache_key]
            if len(existing) >= 1:
                # 从 meta.json 恢复模型选择（覆盖内存，因为可能跨进程）
                _restore_session_model(base, sid, cache_key)
                # 增加引用计数
                _SESSION_REFS[cache_key] = _SESSION_REFS.get(cache_key, 0) + 1
                _touch_session(cache_key)  # 在锁内调用
                return sid, existing

        # 尝试从数据库加载
        loaded = _load_from_db(username, sid, base, workspace)
        if loaded and len(loaded) >= 1:
            if loaded[0].get("role") != "system":
                prompt = _build_system_prompt(username, sid, base, workspace)
                saved_name = _load_session_name(base, sid) or "新会话"
                loaded.insert(0, {"role": "system", "content": prompt, "name": saved_name, "timestamp": now_ts()})
            loaded = _rebuild_tool_messages(loaded)
            _SESSIONS[cache_key] = loaded
            _update_meta_cache(username, sid, workspace, loaded)
            _restore_session_model(base, sid, cache_key)
            # 增加引用计数
            _SESSION_REFS[cache_key] = _SESSION_REFS.get(cache_key, 0) + 1
            _touch_session(cache_key)  # 在锁内调用
            return sid, loaded

        # 不存在且不允许创建
        if not create:
            logger.info(f"[session] 会话 '{sid}' 不存在且 create=False，返回 None")
            return sid, None

        # 全新会话
        import traceback
        caller = ''.join(traceback.format_stack()[-5:-1])
        logger.info(f"[session] 自动创建新会话 sid={sid} ws={workspace} caller=\n{caller}")
        prompt = _build_system_prompt(username, sid, base, workspace)
        _SESSIONS[cache_key] = [{"role": "system", "content": prompt, "name": "新会话", "timestamp": now_ts()}]
        _update_meta_cache(username, sid, workspace, _SESSIONS[cache_key])
        # 增加引用计数
        _SESSION_REFS[cache_key] = _SESSION_REFS.get(cache_key, 0) + 1
        _touch_session(cache_key)  # 在锁内调用
        logger.debug(f"[perf] _get_or_create_session sid={sid} ws={workspace} create={create} time={time.time()-_t0:.3f}s")
        return sid, _SESSIONS[cache_key]

def _run_tool_loop_sync(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                         messages: list[dict], tools: list[dict] | None = None,
                         max_turns: int = 0, abort_event=None,
                         model_name=None, session_lock=None,
                         session_key: str = "",
                         username: str = "",
                         workspace: str | None = None) -> tuple:
    # 获取信号量（限制并发）
    acquired = _concurrent_semaphore.acquire(timeout=30.0)
    if not acquired:
        logger.error(f"[Web] 获取并发信号量超时 session_key={session_key}")
        _safe_queue_put(queue, {
            "event": "error",
            "data": {"error": "服务器繁忙，请稍后重试", "session_id": session_key}
        })
        return None, {}
    
    try:
        # 设置 session_id 到 contextvars（用于日志跟踪）
        from ...logger import set_session_id
        sid = session_key.split(":")[-1] if ":" in session_key else session_key
        set_session_id(sid)
        
        with session_lock:
            with _sessions_lock:
                _SESSION_STATUS[session_key] = "generating"
            logger.debug(f"[Web] _run_tool_loop_sync start key={session_key} workspace={workspace}")
            try:
                from ...tools.update_todos import set_session
                set_session(session_key)

                base = _resolve_base(username, workspace)
                # session_key = f"{username}:{workspace}:{sid}", 取最后一段为 sid
                comp_key = session_key.split(":")[-1] if ":" in session_key else session_key
                comp = _get_or_create_components(username, comp_key, base, workspace)
                register_memory_tools(comp["store"])
                register_history_tools(comp["history_db"], workspace or "default")
                register(comp["skill_loader"])
                if comp.get("project_path"):
                    from ...tools import set_project_path
                    set_project_path(comp["project_path"])
                if comp.get("bus") and comp.get("team_mgr"):
                    register_team(comp["bus"], comp["team_mgr"])
                if comp.get("blackboard"):
                    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
                    register_blackboard(comp["blackboard"], workflow_dirs=workflow_dirs, bus=comp.get("bus"), manager=comp.get("team_mgr"))

                if tools is None:
                    tools = _lead_tool_defs()

                if messages and messages[0]["role"] == "system" and len(messages[0]["content"]) < 50:
                    messages[0]["content"] = _build_system_prompt(username, comp_key, base, workspace)

                disp = WebDisplay(queue, loop, session_id=comp_key)
                # 注册 display 到全局 registry，供 workflow 等工具使用
                from ...tools import _registry
                _registry.register_display(disp)
                
                if comp.get("team_mgr"):
                    comp["team_mgr"].set_display(disp)
                with _sessions_lock:
                    plan_mode = _SESSION_PLAN_MODE.get(session_key, False)
                
                if plan_mode:
                    tools = []
                cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
                ctx = RequestContext(model_config=cfg, display=disp)

                user_msgs = [m for m in messages if m["role"] == "user"]
                if user_msgs:
                    last_user = user_msgs[-1]
                    user_meta = {k: v for k, v in last_user.items() if k not in ("role", "content", "timestamp")}
                    comp["history_db"].append(workspace or "default", comp_key, "user", last_user.get("content", ""), metadata=json.dumps(user_meta) if user_meta else "")

                if len(user_msgs) == 1 and messages[0].get("name", "") in ("", "新会话"):
                    # 处理多模态消息（content 可能是 list）
                    first_content = user_msgs[0].get("content", "")
                    if isinstance(first_content, list):
                        text_parts = [p.get("text", "") for p in first_content if isinstance(p, dict) and p.get("type") == "text"]
                        auto_name = " ".join(text_parts)[:50]
                    else:
                        auto_name = first_content[:50]
                    
                    if auto_name:
                        messages[0]["name"] = auto_name
                        _save_session_name(base, comp_key, auto_name)

                reset_usage()
                logger.debug(f"[Web] run_tool_loop start key={session_key} plan={plan_mode} tools={len(tools)}")
                _deferred_assistant = []

                def _persist(m):
                    if m["role"] == "tool":
                        comp["history_db"].append(workspace or "default", comp_key, "tool", m.get("content", ""), metadata=json.dumps({"name": m.get("name", ""), "tool_call_id": m.get("tool_call_id", "")}))
                    elif m["role"] == "assistant":
                        if m.get("tool_calls"):
                            _deferred_assistant.append(m)
                        else:
                            asst_meta = {}
                            if m.get("thinking"):
                                asst_meta["thinking"] = m["thinking"]
                            comp["history_db"].append(workspace or "default", comp_key, "assistant", m.get("content", ""), metadata=json.dumps(asst_meta) if asst_meta else "")

                msg, _ = run_tool_loop(
                    messages, tools,
                    streaming=STREAMING,
                    display=disp,
                    inject_fn=_inject_todos,
                    abort_event=abort_event,
                    max_turns=max_turns,
                    ctx=ctx,
                    persist_fn=_persist,
                    bus=comp.get("bus"),
                    context_length=cfg.get("context_length", 256000),
                    compactor=comp.get("compactor"),
                )
                _touch_session(session_key)
                
                # 🔧 诊断日志：详细记录 msg 状态
                if msg:
                    content_len = len(msg.get("content") or "")
                    has_tool_calls = bool(msg.get("tool_calls"))
                    logger.debug(f"[Web] run_tool_loop done key={session_key} msg=exists content={content_len} tool_calls={has_tool_calls}")
                else:
                    logger.warning(f"[Web⚠] run_tool_loop done key={session_key} msg=None")


                # ── 兜底：run_tool_loop 结束后等待仍在工作的队友 ──
                # run_tool_loop 内部每轮已检查 inbox 并注入回禀，这里只处理 loop 退出后到达的消息
                bus = comp.get("bus")
                team_mgr = comp.get("team_mgr")
                if bus and team_mgr:
                    from ...config import TIMEOUTS

                    def _inject_inbox(inbox_msgs, label="兜底"):
                        from ...team.loop import format_inbox_messages
                        inbox_text = format_inbox_messages(inbox_msgs)
                        if not inbox_text:
                            return False
                        messages.append({"role": "user", "content": inbox_text, "timestamp": now_ts()})
                        messages.append({"role": "user", "content": "队友回禀已收到。请先 blackboard_read 获取队友写入黑板的结果，再基于回禀和黑板内容回复用户。", "timestamp": now_ts()})
                        comp["history_db"].append(workspace or "default", comp_key, "user", inbox_text)
                        logger.info(f"[Web-Team] {label}回禀注入，继续 run_tool_loop")
                        return True

                    lead_wait = TIMEOUTS.get("lead_wait", 300)
                    poll_interval = TIMEOUTS.get("lead_poll_interval", 5)
                    waited = 0
                    while waited < lead_wait:
                        if abort_event and abort_event.is_set():
                            break
                        with team_mgr.lock:
                            has_working = any(m.get("status") == "working" for m in team_mgr.config.get("members", []))
                        if not has_working:
                            break
                        time.sleep(poll_interval)
                        waited += poll_interval
                        inbox = bus.read_inbox("lead")
                        if inbox and _inject_inbox(inbox):
                            msg, _ = run_tool_loop(
                                messages, tools,
                                streaming=STREAMING, display=disp,
                                inject_fn=_inject_todos, abort_event=abort_event,
                                max_turns=max_turns, ctx=ctx, persist_fn=_persist,
                                bus=bus,
                                context_length=cfg.get("context_length", 256000),
                                compactor=comp.get("compactor"),
                            )
                            logger.info("[Web-Team] 兜底回禀处理后 run_tool_loop done")
                            waited = 0
                    final_inbox = bus.read_inbox("lead")
                    if final_inbox and _inject_inbox(final_inbox, label="最终"):
                        msg, _ = run_tool_loop(
                            messages, tools,
                            streaming=STREAMING, display=disp,
                            inject_fn=_inject_todos, abort_event=abort_event,
                            max_turns=max_turns, ctx=ctx, persist_fn=_persist,
                            bus=bus,
                            context_length=cfg.get("context_length", 256000),
                            compactor=comp.get("compactor"),
                        )
                else:
                    logger.debug(f"[Web-Team] 无 Team 组件，跳过回禀等待")

                tool_results_map = {m.get("tool_call_id", ""): m.get("content", "") for m in messages if m.get("role") == "tool"}
                for am in _deferred_assistant:
                    enriched_tcs = []
                    for tc in am.get("tool_calls", []):
                        tc_copy = {k: v for k, v in tc.items()}
                        tc_id = tc.get("id", "")
                        if tc_id and tc_id in tool_results_map:
                            tc_copy["_result"] = tool_results_map[tc_id]  # 存储完整结果，不再截断
                        enriched_tcs.append(tc_copy)
                    # 🔧 修复：只在有 content 时才写入历史记录（避免写入空的 assistant 消息）
                    if am.get("content"):
                        am_meta = {}
                        if am.get("thinking"):
                            am_meta["thinking"] = am["thinking"]
                        am_meta["tool_calls"] = enriched_tcs
                        comp["history_db"].append(workspace or "default", comp_key, "assistant", am.get("content"), metadata=json.dumps(am_meta))
                if not msg or (not msg.get("content") and not msg.get("tool_calls")):
                    # 🔧 诊断：记录 msg 状态
                    if msg is None:
                        err_text = "⚠ LLM 未返回有效回复（可能因限流或错误）"
                        logger.error(f"[Web⚠] msg=None, 可能是流式错误或中断")
                    elif msg.get("interrupted"):
                        err_text = "⏸ 生成已中断"
                        logger.info(f"[Web] 用户中断生成 sid={comp_key}")
                    elif msg.get("error"):
                        err_text = f"⚠ {msg.get('error')}"
                        logger.error(f"[Web⚠] LLM 错误: {msg.get('error')}")
                    else:
                        err_text = "⚠ LLM 未返回有效回复（可能因限流或错误）"
                        logger.error(f"[Web⚠] msg 存在但无 content/tool_calls, msg keys: {list(msg.keys())}")
                    
                    messages.append({"role": "assistant", "content": err_text, "timestamp": now_ts()})
                    comp["history_db"].append(workspace or "default", comp_key, "assistant", err_text)
                    logger.error(f"[Web] {err_text} sid={comp_key}")
                    
                    # 收集错误上下文
                    error_context = {
                        "session_id": session_key,
                        "workspace": workspace,
                        "message_count": len(messages),
                        "last_user_message": None,
                        "last_tool_calls": [],
                    }
                    
                    # 获取最近的用户消息
                    for m in reversed(messages[-5:]):
                        if m.get("role") == "user":
                            error_context["last_user_message"] = m.get("content", "")[:200]
                            break
                    
                    # 获取最近的工具调用
                    for m in reversed(messages[-10:]):
                        if m.get("role") == "assistant" and m.get("tool_calls"):
                            error_context["last_tool_calls"] = [
                                {"name": tc.get("function", {}).get("name"), "id": tc.get("id")}
                                for tc in m["tool_calls"][:3]
                            ]
                            break
                    
                    # 🔧 直接 put 到 queue（线程安全），确保 WS loop 能 drain 到
                    usage = get_usage()
                    _safe_queue_put(queue, {
                        "event": "complete", 
                        "data": {
                            "prompt_tokens": usage["prompt_tokens"], 
                            "completion_tokens": usage["completion_tokens"],
                            "error": err_text,
                            "error_context": error_context,
                            "session_id": session_key
                        }
                    })
                    return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}
                    
                # 🔧 修复：正常流程的消息处理（之前被错误地放在 return 后面）
                # 🔧 修复：检查 content 是否非空，而不是检查是否存在（空字符串也会通过 msg.get("content") 检查）
                if msg and msg.get("content") and msg["content"].strip() and not any(
                    m.get("role") == "assistant" and m.get("content") == msg["content"]
                    for m in messages[-3:]
                ):
                    if plan_mode:
                        if PLAN.get("approval", True):
                            msg["content"] += "\n\n📋 以上为执行计划，确认后输入 /act 开始执行"
                        else:
                            with _sessions_lock:
                                _SESSION_PLAN_MODE[session_key] = False
                            msg["content"] += "\n\n⚡ 已自动切换到执行模式，开始执行..."
                    asst_ts = now_ts()
                    messages.append({"role": "assistant", "content": msg["content"], "thinking": msg.get("thinking"), "timestamp": asst_ts})

                usage = get_usage()
                if comp["compactor"].should_compact(usage["prompt_tokens"]) or comp["compactor"].should_compact_local(messages):
                    logger.info(f"[Web] 触发压缩: prompt_tokens={usage['prompt_tokens']}, messages={len(messages)}")
                    # 先裁剪工具结果（零开销）
                    pruned = ContextPruner.prune(messages, PruneOptions())
                    if estimate_messages_tokens(pruned) < int(cfg.get("context_length", 256000) * 0.8):
                        messages[:] = pruned
                        logger.info(f"[Web] 裁剪后已低于阈值，跳过压缩: messages={len(messages)}")
                    else:
                        messages[:] = comp["compactor"].compact(llm_chat, pruned, ctx=ctx)
                        logger.info(f"[Web] 压缩完成: messages={len(messages)}")
                    # 防御性重建 system prompt（compact 内部已做，但保留此行为保障 components 重建场景）
                    messages[0]["content"] = _build_system_prompt(username, comp_key, base, workspace)

                _safe_queue_put(queue, {"event": "complete", "data": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}})
                return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}
            finally:
                with _sessions_lock:
                    _SESSION_STATUS[session_key] = "idle"
                    # 减少引用计数
                    refs = _SESSION_REFS.get(session_key, 0)
                    if refs > 0:
                        _SESSION_REFS[session_key] = refs - 1
                # 清理 session_id
                from ...logger import set_session_id
                set_session_id(None)
    except Exception as _sync_err:
        # P1#7: 异常路径兜底发 complete 事件，防止前端 isStreaming 卡 True
        logger.error(f"[Web⚠] _run_tool_loop_sync 异常: {_sync_err}", exc_info=True)
        _safe_queue_put(queue, {
            "event": "complete",
            "data": {"error": f"⚠ 内部错误: {type(_sync_err).__name__}", "session_id": session_key}
        })
        return None, {}
    finally:
        # 防御性重置状态（防止异常路径残留 generating）
        with _sessions_lock:
            if _SESSION_STATUS.get(session_key) == "generating":
                _SESSION_STATUS[session_key] = "idle"
        # 释放并发信号量
        _concurrent_semaphore.release()

# ── Session CRUD ──

@router.post("/session")
async def create_session(body: dict):
    username = body.get("username", "")
    workspace = body.get("workspace") or "default"
    if not username:
        return {"error": "缺少 username"}
    logger.info(f"[session] 用户主动创建会话 ws={workspace}")
    base = _resolve_base(username, workspace)
    sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
    cache_key = _cache_key(username, workspace, sid)
    system_prompt = _build_system_prompt(username, sid, base, workspace)
    _SESSIONS[cache_key] = [{"role": "system", "content": system_prompt, "name": "新会话"}]
    from ...tools.update_todos import set_session
    set_session(cache_key)
    _inject_todos(_SESSIONS[cache_key])
    _update_meta_cache(username, sid, workspace, _SESSIONS[cache_key])
    
    # 持久化到数据库，避免缓存淘汰后会话丢失
    comp = _get_or_create_components(username, sid, base, workspace)
    comp["history_db"].append(workspace, sid, "system", system_prompt, metadata=json.dumps({"name": "新会话"}))
    
    return {"session_id": sid}

@router.get("/sessions")
async def list_sessions(username: str = Query(...), workspace: str | None = Query(default=None)):
    _t0 = time.time()
    try:
        base = _resolve_base(username, workspace)
    except ValueError:
        return {"sessions": []}
    user_dir = base
    if not user_dir.exists():
        return {"sessions": []}
    sessions = []
    for d in sorted(user_dir.iterdir(), key=lambda d: d.name, reverse=True):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        sid = d.name
        cache_key = _cache_key(username, workspace, sid)
        with _sessions_lock:
            cached = _META_CACHE.get(cache_key)
            if cached:
                # 复制一份，避免修改缓存
                cached = dict(cached)
                cached["status"] = _SESSION_STATUS.get(cache_key, "idle")
        if cached:
            sessions.append(cached)
            continue
        msgs = _load_from_db(username, sid, base, workspace) or []
        meta = _build_meta(sid, msgs, username, workspace)
        with _sessions_lock:
            _META_CACHE[cache_key] = meta
        # 跳过空会话（只有 system 消息，无实际对话）
        sessions.append(meta)
    # 默认工作空间无会话时，自动创建一个空会话
    if not sessions and (workspace is None or workspace == "default"):
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
        cache_key = _cache_key(username, workspace, sid)
        system_prompt = _build_system_prompt(username, sid, base, workspace)
        with _sessions_lock:
            _SESSIONS[cache_key] = [{"role": "system", "content": system_prompt, "name": "新会话"}]
        _update_meta_cache(username, sid, workspace, _SESSIONS[cache_key])
        
        # 持久化到数据库
        comp = _get_or_create_components(username, sid, base, workspace)
        comp["history_db"].append(workspace or "default", sid, "system", system_prompt, metadata=json.dumps({"name": "新会话"}))
        
        sessions.append(_build_meta(sid, _SESSIONS[cache_key], username, workspace))
        logger.info(f"[session] default 工作空间无会话，自动创建 sid={sid}")
    sessions.sort(key=lambda s: s.get("updated_at", "") or s.get("created_at", ""), reverse=True)
    logger.debug(f"[perf] list_sessions ws={workspace} count={len(sessions)} time={time.time()-_t0:.3f}s")
    return {"sessions": sessions}


@router.get("/todos")
async def get_todos(username: str = Query(...), workspace: str | None = Query(default=None), session_id: str = Query(...)):
    """获取指定会话的待办列表"""
    from ...tools.update_todos import get_todos as _get_todos
    cache_key = _cache_key(username, workspace, session_id)
    todos = _get_todos(cache_key)
    return {"todos": todos}


@router.delete("/session")
async def delete_session(body: dict):
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        return {"error": "缺少 session_id"}
    ws = workspace or None
    cache_key = _cache_key(username, ws, session_id)
    # 先清 HistoryDB，再删目录
    try:
        base = _resolve_base(username, ws)
        with _sessions_lock:
            comp = _SESSION_COMPONENTS.get(cache_key)
        if comp:
            comp["history_db"].delete_session(ws or "default", session_id)
            # 不再关闭 history_db，因为现在是统一连接池
    except Exception:
        pass
    with _sessions_lock:
        _SESSIONS.pop(cache_key, None)
        _SESSION_COMPONENTS.pop(cache_key, None)
        _META_CACHE.pop(cache_key, None)
        _SESSION_STATUS.pop(cache_key, None)
        _SESSION_MODELS.pop(cache_key, None)
        _SESSION_LOCKS.pop(cache_key, None)
        _SESSION_ACCESS.pop(cache_key, None)
        _SESSION_REFS.pop(cache_key, None)
        _SESSION_ABORTS.pop(cache_key, None)
    wk = _ws_key(username, ws)
    with _sessions_lock:
        remaining = sum(1 for k in _SESSIONS if k.startswith(f"{username}:{ws or 'default'}:") and k != cache_key)
    if remaining == 0:
        with _sessions_lock:
            team_comp = _TEAM_COMPONENTS.get(wk)
        if team_comp:
            team_mgr = team_comp.get("team_mgr")
            if team_mgr:
                for m in team_mgr.config.get("members", []):
                    if m.get("status") in ("idle", "working"):
                        team_comp["bus"].send("lead", m["name"], "会话结束，请退出。", "shutdown_request")
    from ...tools.update_todos import cleanup_session
    cleanup_session(cache_key)
    session_dir = _resolve_base(username, ws) / session_id
    if session_dir.exists():
        import shutil
        shutil.rmtree(session_dir, ignore_errors=True)
    return {"status": "ok"}

@router.post("/sessions/batch_delete")
async def batch_delete_sessions(body: dict):
    username = body.get("username", "")
    session_ids = body.get("session_ids", [])
    workspace = body.get("workspace", "") or "default"
    if not username or not session_ids:
        return {"error": "参数不完整", "deleted": 0}
    ws = workspace or None
    base = _resolve_base(username, ws)
    deleted = 0
    for sid in session_ids:
        cache_key = _cache_key(username, ws, sid)
        try:
            comp = _SESSION_COMPONENTS.get(cache_key)
            if comp:
                comp["history_db"].delete_session(workspace or "default", sid)
                # 不再关闭 history_db，因为现在是统一连接池
        except Exception:
            pass
        with _sessions_lock:
            _SESSIONS.pop(cache_key, None)
        _SESSION_COMPONENTS.pop(cache_key, None)
        _META_CACHE.pop(cache_key, None)
        _SESSION_STATUS.pop(cache_key, None)
        _SESSION_MODELS.pop(cache_key, None)
        _SESSION_LOCKS.pop(cache_key, None)
        _SESSION_ACCESS.pop(cache_key, None)
        _SESSION_REFS.pop(cache_key, None)  # P0#2
        from ...tools.update_todos import cleanup_session
        cleanup_session(cache_key)
        session_dir = base / sid
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
            deleted += 1
    return {"status": "ok", "deleted": deleted}

@router.patch("/session/rename")
async def rename_session(body: dict):
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    name = body.get("name", "").strip()
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id or not name:
        return {"error": "参数不完整"}
    ws = workspace or None
    base = _resolve_base(username, ws)
    _, messages = _get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    messages[0]["name"] = name
    _update_meta_cache(username, session_id, ws, messages)
    _save_session_name(base, session_id, name)
    return {"status": "ok", "name": name}

# ── WebSocket endpoint (parallel) ──

@router.websocket("/chat/ws")
async def chat_ws_endpoint(ws: WebSocket):
    await ws.accept()
    # 前端连接时应在第一条消息中发送用户名，否则拒绝
    _active_tasks: dict[str, asyncio.Task] = {}
    _ws_abort_keys: list[str] = []
    ws_closed = False
    _write_lock = asyncio.Lock()
    _ws_username: str | None = None

    async def _send(data: dict):
        async with _write_lock:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.warning(f'[Web] _send failed: {e}, event={data.get("event")}')

    async def _run_chat(sid: str, username: str, user_message: str, ws_name: str | None = None, images: list | None = None):
        logger.info(f"[Web] WS _run_chat sid={sid} user={username} ws={ws_name} images={len(images) if images else 0}")
        session_key = _cache_key(username, ws_name, sid)
        base = _resolve_base(username, ws_name)
        messages = _get_or_create_session(username, sid, base, ws_name)[1]
        ts = now_ts()
        
        # 构造用户消息（可能包含图片）
        user_msg: dict = {"role": "user", "content": user_message, "timestamp": ts}
        if images and len(images) > 0:
            # 转换为 OpenAI 格式：content 是列表
            content_blocks = [{"type": "text", "text": user_message}]
            for img in images:
                data_url = img.get("dataUrl", "")
                if data_url.startswith("data:"):
                    content_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": data_url}
                    })
            user_msg["content"] = content_blocks
        
        messages.append(user_msg)
        _get_or_create_components(username, sid, base, ws_name)

        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)  # 添加队列上限，防止内存溢出
        loop = asyncio.get_event_loop()
        abort_event = threading.Event()
        with _sessions_lock:
            _SESSION_ABORTS[session_key] = abort_event
            model_name = _SESSION_MODELS.get(session_key)
        _ws_abort_keys.append(session_key)

        s_lock = _get_session_lock(username, ws_name, sid)
        from ...config import RUNNER
        max_turns_web = RUNNER.get("max_turns", 20)
        future = loop.run_in_executor(
            None, _run_tool_loop_sync, queue, loop, messages, None, max_turns_web, abort_event, model_name, s_lock, session_key, username, ws_name
        )

        complete_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        aborted = False
        got_terminal = False  # 是否已收到终端事件（complete/done/aborted）
        try:
            while True:
                if abort_event.is_set():
                    aborted = True
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.15)
                    event["data"]["session_id"] = sid
                    if event["event"] == "complete" and "prompt_tokens" in event["data"]:
                        complete_usage = {"prompt_tokens": event["data"]["prompt_tokens"], "completion_tokens": event["data"].get("completion_tokens", 0)}
                    if event["event"] in ("done", "aborted", "error", "complete"):
                        logger.debug(f'[Web] terminal event from queue sid={sid} event={event["event"]}')
                    await _send(event)
                    if event["event"] in ("done", "aborted", "complete"):
                        got_terminal = True
                        break
                except asyncio.TimeoutError:
                    if future.done():
                        # Drain remaining queue items before exiting
                        # (call_soon_threadsafe events may not have been processed yet)
                        for _ in range(10):
                            try:
                                event = queue.get_nowait()
                                event["data"]["session_id"] = sid
                                if event["event"] == "complete" and "prompt_tokens" in event["data"]:
                                    complete_usage = {"prompt_tokens": event["data"]["prompt_tokens"], "completion_tokens": event["data"].get("completion_tokens", 0)}
                                if event["event"] in ("done", "aborted", "error", "complete"):
                                    logger.debug(f'[Web] drained terminal event sid={sid} event={event["event"]}')
                                await _send(event)
                                if event["event"] in ("done", "aborted", "complete"):
                                    got_terminal = True
                                    break
                            except asyncio.QueueEmpty:
                                break
                        else:
                            logger.debug(f"[Web] future.done() drain exhausted sid={sid}")
                        break
        except Exception as e:
            logger.error(f"[Web] WS chat task error: {e}", exc_info=True)
            await _send({"event": "error", "data": {"error": str(e), "session_id": sid}})

        if aborted:
            logger.info(f"[Web] chat aborted sid={sid}")
            await _send({"event": "aborted", "data": {"session_id": sid}})
            try:
                future.cancel()
            except Exception:
                pass

        usage = complete_usage

        with _sessions_lock:
            _LAST_USAGE[session_key] = usage
            _SESSION_ABORTS.pop(session_key, None)
        _update_meta_cache(username, sid, ws_name, messages)

        if not aborted and not got_terminal:
            # 只在未从 queue 收到终端事件时发 done（避免 complete + done 双事件）
            logger.debug(f"[Web] sending done sid={sid} usage={usage}")
            await _send({
                "event": "done",
                "data": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"], "session_id": sid}
            })
            logger.debug(f"[Web] done sent ok sid={sid}")

        _active_tasks.pop(session_key, None)

    async def _reader():
        nonlocal ws_closed, _ws_username
        try:
            while not ws_closed:
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        await _send({"event": "error", "data": {"error": "无效 JSON"}})
                        continue

                    msg_type = data.get("type")

                    if msg_type == "login":
                        u = data.get("username", "").strip()
                        if u:
                            _ws_username = u
                        continue

                    # 处理心跳 ping
                    if msg_type == "ping":
                        await _send({"event": "pong", "data": {}})
                        continue

                    if not _ws_username:
                        await _send({"event": "error", "data": {"error": "请先发送 login 消息"}})
                        continue

                    if msg_type == "abort":
                        abort_sid = data.get("session_id")
                        abort_username = _ws_username
                        abort_ws = data.get("workspace")
                        if abort_sid:
                            evt = _SESSION_ABORTS.get(_cache_key(abort_username, abort_ws, abort_sid))
                            if evt:
                                evt.set()
                        continue

                    if msg_type != "chat":
                        continue

                    user_message = data.get("message", "").strip()
                    username = _ws_username
                    session_id = data.get("session_id")
                    images = data.get("images")  # 获取图片数据
                    
                    # 处理 /compact 命令
                    if user_message == "/compact":
                        ws_name = data.get("workspace")
                        if not session_id:
                            await _send({"event": "error", "data": {"error": "请先选择会话"}})
                            continue
                        
                        sid, messages = _get_or_create_session(username, session_id, workspace=ws_name, create=False)
                        if messages is None:
                            await _send({"event": "error", "data": {"error": f"会话 {session_id} 不存在"}})
                            continue
                        
                        comp = _get_or_create_components(username, sid, _resolve_base(username, ws_name), ws_name)
                        non_system = [m for m in messages if m["role"] != "system"]
                        
                        if len(non_system) <= comp["compactor"].keep_recent:
                            await _send({"event": "info", "data": {"message": f"消息数({len(non_system)})未超过保留阈值({comp['compactor'].keep_recent})，无需压缩", "session_id": sid}})
                            continue
                        
                        before = len(non_system)
                        
                        # 🔧 修复：获取用户选择的模型，创建正确的 ctx
                        session_key = _cache_key(username, ws_name, sid)
                        model_name = _SESSION_MODELS.get(session_key)
                        cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
                        ctx = RequestContext(model_config=cfg, display=None)
                        
                        # 🔧 修复：添加错误处理，避免异常导致连接断开
                        try:
                            from ...llm import chat
                            messages[:] = comp["compactor"].compact(chat, messages, ctx=ctx)
                            after = len([m for m in messages if m["role"] != "system"])
                            
                            await _send({"event": "info", "data": {"message": f"压缩完成：{before} → {after} 条消息（摘要 {before - after} 条）", "session_id": sid}})
                            # 🔧 修复：发送 done 事件结束流式状态
                            await _send({"event": "done", "data": {"session_id": sid}})
                        except Exception as e:
                            logger.error(f"[Web] /compact 失败: {e}", exc_info=True)
                            await _send({"event": "error", "data": {"error": f"压缩失败: {str(e)}"}})
                            await _send({"event": "done", "data": {"session_id": sid}})
                        
                        continue
                    
                    if not user_message and not images:
                        await _send({"event": "error", "data": {"error": "消息不能为空"}})
                        continue

                    ws_name = data.get("workspace")
                    if session_id:
                        # 有 session_id，尝试加载（不自动创建）
                        sid, _ = _get_or_create_session(username, session_id, workspace=ws_name, create=False)
                        if _ is None:
                            # 会话不存在，返回错误
                            await _send({"event": "error", "data": {"error": f"会话 {session_id} 不存在", "session_id": session_id}})
                            continue
                    elif (ws_name or "default") == "default":
                        # default 工作空间且无 session_id，自动创建
                        sid, _ = _get_or_create_session(username, session_id, workspace=ws_name)
                    else:
                        # 非 default 工作空间且无 session_id，返回错误
                        await _send({"event": "error", "data": {"error": "非 default 工作空间请先新建会话"}})
                        continue
                    task_key = _cache_key(username, ws_name, sid)
                    if task_key in _active_tasks:
                        await _send({"event": "error", "data": {"error": "该会话正在生成中", "session_id": sid}})
                        continue

                    task = asyncio.create_task(_run_chat(sid, username, user_message, ws_name, images))
                    _active_tasks[task_key] = task

                except asyncio.TimeoutError:
                    pass
                except WebSocketDisconnect:
                    ws_closed = True
                    break
                except Exception as _e:
                    logger.error(f"[Web] WS reader error: {_e}")
                    ws_closed = True
                    break
        except Exception as _e:
            logger.error(f"[Web] WS outer error: {_e}")
        finally:
            if not ws_closed:
                ws_closed = True

    reader_task = asyncio.create_task(_reader())

    try:
        await reader_task
    except Exception as e:
        pass
    finally:
        ws_closed = True
        for task in _active_tasks.values():
            task.cancel()
        reader_task.cancel()
        for sid_key in _ws_abort_keys:
            evt = _SESSION_ABORTS.pop(sid_key, None)
            if evt:
                evt.set()
        _ws_abort_keys.clear()
        _active_tasks.clear()

@router.get("/chat/search")
async def chat_search(keyword: str = Query(default=""), session_id: str = Query(default="default"),
                       username: str = Query(...), workspace: str = Query(default=""),
                       date_from: str = Query(default=""), date_to: str = Query(default=""),
                       limit: int = Query(default=20)):
    if not username:
        return {"results": []}
    base = _resolve_base(username, workspace or None)
    comp = _get_or_create_components(username, session_id, base, workspace or None)
    results = comp["history_db"].search(keyword, workspace=workspace or "", date_from=date_from, date_to=date_to, limit=limit)
    return {"results": results}

# ── History & Reset ──

@router.get("/chat/history")
async def chat_history(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")):
    _t0 = time.time()
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    base = _resolve_base(username, workspace or None)
    cache_key = _cache_key(username, workspace or None, session_id)
    comp = _get_or_create_components(username, session_id, base, workspace or None)
    messages = comp["history_db"].load_session(workspace or "default", session_id, limit=WEB.get("history_limit", 200))
    if not messages:
        return {"session_id": session_id, "history": []}
    logger.info(f"[chat_history] sid={session_id} ws={workspace} base={base} msgs={len(messages)} time={time.time()-_t0:.3f}s")
    non_system = [m for m in messages if m["role"] not in ("system", "tool")]
    history = []
    for m in non_system:
        entry: dict = {"role": m["role"]}
        content = m.get("content")
        
        # 处理多模态消息（content 可能是 list）
        if isinstance(content, list):
            # 提取文本和图片
            text_parts = []
            images = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        img_url = part.get("image_url", {}).get("url", "")
                        if img_url:
                            images.append({"dataUrl": img_url, "name": "", "size": 0})
            entry["content"] = "\n".join(text_parts)
            if images:
                entry["images"] = images
        else:
            if content:
                entry["content"] = content
        
        if m.get("timestamp"):
            entry["timestamp"] = m["timestamp"]
        if m.get("thinking"):
            entry["thinking"] = m["thinking"]
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        history.append(entry)
    return {"session_id": session_id, "history": history}

@router.post("/chat/reset")
async def chat_reset(body: dict | None = None):
    body = body or {}
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    ws = workspace or None
    base = _resolve_base(username, ws)
    sid, messages = _get_or_create_session(username, session_id, base, ws, create=False)
    if not messages:
        return {"error": f"会话 '{session_id}' 不存在"}
    system_content = messages[0]["content"]
    old_name = messages[0].get("name", "")
    cache_key = _cache_key(username, ws, sid)
    _SESSIONS[cache_key] = [{"role": "system", "content": system_content, "name": old_name or "新会话", "timestamp": now_ts()}]
    _inject_todos(_SESSIONS[cache_key])
    _update_meta_cache(username, sid, ws, _SESSIONS[cache_key])
    with _sessions_lock:
        _SESSION_COMPONENTS.pop(cache_key, None)
        _SESSION_REFS.pop(cache_key, None)  # P0#2
    return {"status": "ok", "session_id": sid}

@router.get("/chat/export")
async def chat_export(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default=""), limit: int = Query(default=0), include_thinking: bool = Query(default=False), include_tools: bool = Query(default=False)):
    from fastapi.responses import JSONResponse
    if not username:
        return JSONResponse({"error": "缺少 username"}, status_code=400)
    if not session_id:
        return JSONResponse({"error": "缺少 session_id"}, status_code=400)
    try:
        base = _resolve_base(username, workspace or None)
    except Exception as e:
        logger.error(f"[export] _resolve_base error: {e}")
        return JSONResponse({"error": f"工作空间错误: {e}"}, status_code=400)
    comp = _get_or_create_components(username, session_id, base, workspace or None)
    messages = comp["history_db"].load_session(workspace or "default", session_id, limit=limit) or []
    if not messages:
        return JSONResponse({"error": f"会话 '{session_id}' 不存在或无消息"}, status_code=404)

    session_name = ""
    try:
        session_name = _load_session_name(base, session_id)
    except Exception:
        pass
    if not session_name:
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                session_name = m["content"][:50]
                break
    if not session_name:
        session_name = session_id

    lines = [f"# {session_name}\n"]

    for m in messages:
        role = m.get("role", "")
        content = m.get("content") or ""
        ts = m.get("timestamp", "")

        if role == "system":
            continue
        if role == "tool":
            continue

        if role == "user":
            label = f"**🧑 用户**"
            if ts:
                label += f"  `{ts}`"
            lines.append(f"\n{label}\n\n{content}\n")
        elif role == "assistant":
            thinking = m.get("thinking")
            tool_calls = m.get("tool_calls")
            has_thinking = include_thinking and thinking
            has_tools = include_tools and tool_calls
            if not content and not has_thinking and not has_tools:
                continue
            label = f"**🤖 助手**"
            if ts:
                label += f"  `{ts}`"
            lines.append(f"\n{label}\n")
            if has_thinking:
                thinking_text = thinking if isinstance(thinking, str) else str(thinking)
                lines.append(f"\n<details>\n<summary>💭 思考过程</summary>\n\n{thinking_text}\n\n</details>\n")
            if has_tools:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args = str(fn.get("arguments", ""))
                    result = tc.get("_result", "")
                    lines.append(f"\n> 🔧 **{name}**({args[:200]})\n")
                    if result:
                        lines.append(f"> 结果: {result[:500]}\n")
            if content:
                lines.append(f"\n{content}\n")

    md_content = "\n".join(lines)

    from fastapi.responses import Response
    from urllib.parse import quote
    safe_name = session_name.replace("/", "-").replace(" ", "-")[:60]
    encoded_name = quote(safe_name)
    return Response(
        content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}.md"},
    )
