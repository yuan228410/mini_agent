"""Web 端工具循环运行器 — 从 chat.py 提取的 _run_tool_loop_sync"""
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import DATA_DIR, MODEL_CONFIG, STREAMING, PLAN, PACKAGE_DIR, RequestContext, get_model_config
from ..llm import get_usage, reset_usage, chat as llm_chat
from ..llm.base import estimate_messages_tokens
from ..memory.context_pruner import ContextPruner, PruneOptions
from ..runner import run_tool_loop
from ..tools import register_memory_tools, register_history_tools, register, inject_todos as _inject_todos
from ..tools import register_team, register_blackboard
from ..logger import logger
from .display import WebDisplay
from .session_manager import (
    SessionManager, cache_key, safe_queue_put,
    resolve_base, get_or_create_components, build_system_prompt,
    lead_tool_defs, _save_session_name, _update_meta_cache,
)
from ..utils import now_ts

# 线程池配置
_MAX_CONCURRENT_SESSIONS = 10
_executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_SESSIONS, thread_name_prefix="chat-")
_concurrent_semaphore = threading.Semaphore(_MAX_CONCURRENT_SESSIONS)


def run_tool_loop_sync(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                       messages: list[dict], tools: list[dict] | None = None,
                       max_turns: int = 0, abort_event=None,
                       model_name=None, session_lock=None,
                       session_key: str = "",
                       username: str = "",
                       workspace: str | None = None) -> tuple:
    """在后台线程中运行工具循环，将事件推入 queue 供 WS 消费"""
    acquired = _concurrent_semaphore.acquire(timeout=30.0)
    if not acquired:
        logger.error(f"[Web] 获取并发信号量超时 session_key={session_key}")
        safe_queue_put(queue, {
            "event": "error",
            "data": {"error": "服务器繁忙，请稍后重试", "session_id": session_key}
        })
        return None, {}

    try:
        from ..logger import set_session_id
        sid = session_key.split(":")[-1] if ":" in session_key else session_key
        set_session_id(sid)

        with session_lock:
            sm = SessionManager.instance()
            sm.set_status(session_key, "generating")

            logger.debug(f"[Web] run_tool_loop_sync start key={session_key} workspace={workspace}")
            try:
                from ..tools.update_todos import set_session
                set_session(session_key)

                base = resolve_base(username, workspace)
                comp_key = session_key.split(":")[-1] if ":" in session_key else session_key
                comp = get_or_create_components(username, comp_key, base, workspace)
                register_memory_tools(comp["store"])
                register_history_tools(comp["history_db"], workspace or "default")
                register(comp["skill_loader"])
                if comp.get("project_path"):
                    from ..tools import set_project_path
                    set_project_path(comp["project_path"])
                if comp.get("bus") and comp.get("team_mgr"):
                    register_team(comp["bus"], comp["team_mgr"])
                if comp.get("blackboard"):
                    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
                    register_blackboard(comp["blackboard"], workflow_dirs=workflow_dirs, bus=comp.get("bus"), manager=comp.get("team_mgr"))

                if tools is None:
                    tools = lead_tool_defs()

                if messages and messages[0]["role"] == "system" and len(messages[0]["content"]) < 50:
                    messages[0]["content"] = build_system_prompt(username, comp_key, base, workspace)

                disp = WebDisplay(queue, loop, session_id=comp_key)
                from ..tools import _registry
                _registry.register_display(disp)

                if comp.get("team_mgr"):
                    comp["team_mgr"].set_display(disp)

                plan_mode = sm.get_plan_mode(session_key)
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

                # 使用 HistoryPersister 统一持久化
                from ..core.persister import HistoryPersister
                persister = HistoryPersister(comp["history_db"], workspace or "default", comp_key)

                msg, _ = run_tool_loop(
                    messages, tools,
                    streaming=STREAMING,
                    display=disp,
                    inject_fn=_inject_todos,
                    abort_event=abort_event,
                    max_turns=max_turns,
                    ctx=ctx,
                    persist_fn=persister,
                    bus=comp.get("bus"),
                    context_length=cfg.get("context_length", 256000),
                    compactor=comp.get("compactor"),
                )

                # flush deferred assistant
                persister.flush_deferred(messages)

                sm.touch(session_key)

                if msg:
                    content_len = len(msg.get("content") or "")
                    has_tool_calls = bool(msg.get("tool_calls"))
                    logger.debug(f"[Web] run_tool_loop done key={session_key} msg=exists content={content_len} tool_calls={has_tool_calls}")
                else:
                    logger.warning(f"[Web⚠] run_tool_loop done key={session_key} msg=None")

                # ── 队友兜底轮询 ──
                # 错误时跳过轮询，直接进入错误处理
                bus = comp.get("bus")
                team_mgr = comp.get("team_mgr")
                if bus and team_mgr and msg is not None and not msg.get("error"):
                    from ..config import TIMEOUTS

                    def _inject_inbox(inbox_msgs, label="兜底"):
                        from ..team.loop import format_inbox_messages
                        inbox_text = format_inbox_messages(inbox_msgs)
                        if not inbox_text:
                            return False
                        messages.append({"role": "user", "content": inbox_text, "timestamp": now_ts()})
                        messages.append({"role": "user", "content": "队友回禀已收到。请先 blackboard_read 获取队友写入黑板的结果，再基于回禀和黑板内容回复用户。", "timestamp": now_ts()})
                        logger.info(f"[Web/队友] {label}注入 {len(inbox_msgs)} 条回禀")
                        return True

                    lead_event = threading.Event()
                    deadline = time.monotonic() + TIMEOUTS.get("lead_wait", 1800)
                    poll_interval = TIMEOUTS.get("lead_poll_interval", 2)

                    while time.monotonic() < deadline:
                        inbox = bus.read_inbox("lead")
                        if inbox:
                            if _inject_inbox(inbox):
                                reset_usage()
                                msg2, _ = run_tool_loop(
                                    messages, tools,
                                    streaming=STREAMING, display=disp,
                                    inject_fn=_inject_todos, abort_event=abort_event,
                                    max_turns=3, ctx=ctx, persist_fn=persister,
                                    bus=bus, context_length=cfg.get("context_length", 256000),
                                    compactor=comp.get("compactor"),
                                )
                                persister.flush_deferred(messages)
                                if msg2 and msg2.get("content"):
                                    msg = msg2
                        if abort_event and abort_event.is_set():
                            break
                        lead_event.wait(timeout=poll_interval)

                # ── 错误处理 ──
                if not msg or (not msg.get("content") and not msg.get("tool_calls")):
                    if msg is None:
                        err_text = "⚠ LLM 未返回有效回复（可能因限流或错误）"
                        logger.error(f"[Web⚠] msg=None, 可能是流式错误或中断")
                    elif msg.get("interrupted"):
                        err_text = "⏸ 生成已中断"
                    elif msg.get("error"):
                        err_text = f"⚠ {msg.get('error')}"
                    else:
                        err_text = "⚠ LLM 未返回有效回复（可能因限流或错误）"

                    messages.append({"role": "assistant", "content": err_text, "timestamp": now_ts()})
                    comp["history_db"].append(workspace or "default", comp_key, "assistant", err_text)

                    error_context = {
                        "session_id": session_key,
                        "workspace": workspace,
                        "message_count": len(messages),
                        "last_user_message": None,
                        "last_tool_calls": [],
                    }
                    for m in reversed(messages[-5:]):
                        if m.get("role") == "user":
                            error_context["last_user_message"] = m.get("content", "")[:200]
                            break
                    for m in reversed(messages[-10:]):
                        if m.get("role") == "assistant" and m.get("tool_calls"):
                            error_context["last_tool_calls"] = [
                                {"name": tc.get("function", {}).get("name"), "id": tc.get("id")}
                                for tc in m["tool_calls"][:3]
                            ]
                            break

                    usage = get_usage()
                    logger.warning(f"[Web] error path: err={err_text[:80]}")
                    safe_queue_put(queue, {
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

                # ── 正常流程 ──
                if msg and msg.get("content") and msg["content"].strip() and not any(
                    m.get("role") == "assistant" and m.get("content") == msg["content"]
                    for m in messages[-3:]
                ):
                    if plan_mode:
                        if PLAN.get("approval", True):
                            msg["content"] += "\n\n📋 以上为执行计划，确认后输入 /act 开始执行"
                        else:
                            sm.set_plan_mode(session_key, False)
                            msg["content"] += "\n\n⚡ 已自动切换到执行模式，开始执行..."
                    asst_ts = now_ts()
                    messages.append({"role": "assistant", "content": msg["content"], "thinking": msg.get("thinking"), "timestamp": asst_ts})

                # 使用 maybe_compact 统一压缩逻辑
                usage = get_usage()
                if comp["compactor"].maybe_compact(messages, usage["prompt_tokens"], llm_chat, ctx, cfg.get("context_length", 256000)):
                    messages[0]["content"] = build_system_prompt(username, comp_key, base, workspace)

                safe_queue_put(queue, {"event": "complete", "data": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}})
                return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}

            finally:
                sm.set_status(session_key, "idle")
                sm.dec_ref(session_key)
                from ..logger import set_session_id
                set_session_id(None)

    except Exception as _sync_err:
        logger.error(f"[Web⚠] run_tool_loop_sync 异常: {_sync_err}", exc_info=True)
        safe_queue_put(queue, {
            "event": "complete",
            "data": {"error": f"⚠ 内部错误: {type(_sync_err).__name__}", "session_id": session_key}
        })
        return None, {}

    finally:
        sm = SessionManager.instance()
        if sm.get_status(session_key) == "generating":
            sm.set_status(session_key, "idle")
        _concurrent_semaphore.release()
