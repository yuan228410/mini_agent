"""Web 端工具循环运行器 — 从 chat.py 提取的 _run_tool_loop_sync"""
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..llm import get_usage, reset_usage, chat as llm_chat
from ..core import ApplicationService, RunTurnOptions, build_session_runtime
from ..core.events import DisplayEvent, DisplayEventType
from ..core.runtime_context import SessionIdentity
from ..core.runtime_types import MessageDict, ToolDefinition
from ..tools import inject_todos as _inject_todos
from ..logger import logger, set_session_id
from ..plan.artifact_parser import strip_artifact_blocks
from ..plan.prompts import build_plan_user_message
from ..plan.service import PlanService
from ..plan.store import PlanStore
from ..plan.tool_policy import ToolPolicy, filter_tools
from .display import WebDisplay
from .session_manager import (
    SessionManager, cache_key, safe_queue_put,
    resolve_base, get_or_create_components, build_system_prompt,
    _save_session_name, _update_meta_cache,
)
from .runtime_helpers import settings_for_model
from ..utils import now_ts

# 线程池配置
_MAX_CONCURRENT_SESSIONS = 10
_executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_SESSIONS, thread_name_prefix="chat-")
_concurrent_semaphore = threading.Semaphore(_MAX_CONCURRENT_SESSIONS)


def _ws_event(event: DisplayEventType, **data) -> dict:
    return DisplayEvent(event, data).to_wire()


def run_tool_loop_sync(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                       messages: list[MessageDict], tools: list[ToolDefinition] | None = None,
                       max_turns: int = 0, abort_event=None,
                       model_name=None, session_lock=None,
                       session_key: str = "",
                       username: str = "",
                       workspace: str | None = None,
                       plan_turn: bool = False,
                       approved_plan: dict | None = None) -> tuple:
    """在后台线程中运行工具循环，将事件推入 queue 供 WS 消费"""
    acquired = _concurrent_semaphore.acquire(timeout=30.0)
    if not acquired:
        logger.error(f"[Web] 获取并发信号量超时 session_key={session_key}")
        safe_queue_put(queue, {
            "event": "error",
            "data": {"error": "服务器繁忙，请稍后重试", "session_id": session_key}
        }, loop)
        return None, {}

    try:
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

                if messages and messages[0]["role"] == "system" and len(messages[0]["content"]) < 50:
                    messages[0]["content"] = build_system_prompt(username, comp_key, base, workspace)

                disp = WebDisplay(queue, loop, session_id=comp_key, suppress_text=plan_turn)
                from .deps import SUBAGENT_LOADER, _MCP_LOADER
                base_settings = comp.get("settings")
                runtime_settings = settings_for_model(base_settings, model_name) if base_settings else None
                cfg = runtime_settings.model.to_dict() if runtime_settings else None
                runtime = build_session_runtime(
                    identity=SessionIdentity(
                        username=username or "default",
                        workspace=workspace or "default",
                        session_id=comp_key,
                        project_path=comp.get("project_path") or "",
                    ),
                    messages=messages,
                    display=disp,
                    history_db=comp.get("history_db"),
                    memory_store=comp.get("store"),
                    skill_loader=comp.get("skill_loader"),
                    subagent_loader=SUBAGENT_LOADER,
                    bus=comp.get("bus"),
                    team_mgr=comp.get("team_mgr"),
                    blackboard=comp.get("blackboard"),
                    abort_event=abort_event,
                    model_config=cfg,
                    settings=runtime_settings,
                    mcp_loader=_MCP_LOADER,
                    compactor=comp.get("compactor"),
                    context_builder=comp.get("ctx_builder"),
                )
                disp._usage_provider = runtime.usage_collector.snapshot if runtime.usage_collector else None
                if runtime.execution_budget:
                    disp._stream_flush_ms = runtime.execution_budget.stream_chunk_flush_ms
                    disp._stream_max_chars = runtime.execution_budget.stream_chunk_max_chars
                tool_registry = runtime.tool_registry
                ctx = runtime.request_context
                settings = runtime.settings

                if tools is None:
                    tools = [d for d in tool_registry.get_definitions() if d["function"]["name"] not in ("read_inbox", "list_teammates")]
                all_tools = tools

                plan_state = sm.get_plan_state(session_key)
                if plan_turn:
                    tools = filter_tools(all_tools, ToolPolicy.PLAN_READONLY)
                    plan_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
                    if plan_user and isinstance(plan_user.get("content"), str):
                        plan_user["_plan_original_content"] = plan_user.get("content", "")
                        plan_user["content"] = build_plan_user_message(
                            plan_user.get("content", ""),
                            current_plan=plan_state.current_plan,
                            selected_option_id=plan_state.selected_option_id,
                        )
                else:
                    tools = filter_tools(all_tools, ToolPolicy.EXECUTION)
                    if approved_plan:
                        plan_svc = PlanService()
                        plan_svc.seed_execution_todos(artifact=approved_plan, session_key=session_key, display=disp)
                        messages.append({"role": "user", "content": plan_svc.execution_instruction(approved_plan), "timestamp": now_ts(), "_internal": True})

                user_msgs = [m for m in messages if m["role"] == "user" and not m.get("_internal")]
                if user_msgs:
                    last_user = user_msgs[-1]
                    user_meta = {k: v for k, v in last_user.items() if k not in ("role", "content", "timestamp", "_plan_original_content")}
                    persisted_user_content = last_user.get("_plan_original_content", last_user.get("content", ""))
                    comp["history_db"].append(workspace or "default", comp_key, "user", persisted_user_content, metadata=json.dumps(user_meta) if user_meta else "")

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

                logger.debug(f"[Web] run_tool_loop start key={session_key} plan_turn={plan_turn} tools={len(tools)}")

                result = ApplicationService().run_turn(
                    runtime=runtime,
                    tools=tools,
                    plan_store=PlanStore(comp["history_db"], workspace or "default", comp_key) if (plan_turn or approved_plan) else None,
                    plan_state=sm,
                    user_text_for_history=(user_msgs[-1].get("_plan_original_content", user_msgs[-1].get("content", "")) if user_msgs else None),
                    options=RunTurnOptions(
                        streaming=None,
                        abort_event=abort_event,
                        max_turns=max_turns,
                        plan_turn=plan_turn,
                        approved_plan=approved_plan,
                        context_length=None,
                        persist_user_history=False,
                        plan_session_key=session_key,
                    ),
                )
                msg = result.message

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
                if bus and team_mgr and msg is not None and not msg.get("error") and msg.get("tool_calls"):
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
                    timeout_settings = settings.timeouts if settings else None
                    deadline = time.monotonic() + (timeout_settings.lead_wait if timeout_settings else 1800)
                    poll_interval = timeout_settings.lead_poll_interval if timeout_settings else 2

                    while time.monotonic() < deadline:
                        inbox = bus.read_inbox("lead")
                        if inbox:
                            if _inject_inbox(inbox):
                                result2 = ApplicationService().run_turn(
                                    runtime=runtime,
                                    tools=tools,
                                    options=RunTurnOptions(
                                        streaming=None,
                                        abort_event=abort_event,
                                        max_turns=3,
                                        context_length=None,
                                        persist_user_history=False,
                                    ),
                                )
                                msg2 = result2.message
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
                    safe_queue_put(queue, _ws_event(
                        DisplayEventType.COMPLETE,
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        error=err_text,
                        error_context=error_context,
                        session_id=session_key,
                    ), loop)
                    return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}

                # ── 正常流程 ──
                raw_plan_text = result.raw_plan_text if plan_turn else None
                if msg and msg.get("content") and msg["content"].strip():
                    if plan_turn:
                        display_content = "计划已更新。请在消息区按向导一步步选择；所有关键选择完成后，最终计划会出现在右侧面板等待确认执行。"
                        msg["content"] = display_content
                        msg["kind"] = "plan_discussion"
                        safe_queue_put(queue, _ws_event(DisplayEventType.TEXT, content=display_content, session_id=comp_key), loop)
                        for m in reversed(messages):
                            if m.get("role") == "assistant" and m.get("content") == raw_plan_text:
                                m["content"] = display_content
                                m["kind"] = "plan_discussion"
                                break
                    elif not any(
                        m.get("role") == "assistant" and m.get("content") == msg["content"]
                        for m in messages[-3:]
                    ):
                        asst_ts = now_ts()
                        messages.append({"role": "assistant", "content": msg["content"], "thinking": msg.get("thinking"), "timestamp": asst_ts, "kind": "chat"})

                usage = result.usage
                safe_queue_put(queue, _ws_event(DisplayEventType.COMPLETE, prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"]), loop)
                return msg, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}

            finally:
                try:
                    if 'ctx' in locals() and ctx is not None:
                        ctx.close()
                finally:
                    sm.set_status(session_key, "idle")
                    sm.dec_ref(session_key)
                    set_session_id(None)

    except Exception as _sync_err:
        logger.error(f"[Web⚠] run_tool_loop_sync 异常: {_sync_err}", exc_info=True)
        safe_queue_put(queue, _ws_event(
            DisplayEventType.COMPLETE,
            error=f"⚠ 内部错误: {type(_sync_err).__name__}",
            session_id=session_key,
        ), loop)
        return None, {}

    finally:
        sm = SessionManager.instance()
        if sm.get_status(session_key) == "generating":
            sm.set_status(session_key, "idle")
        _concurrent_semaphore.release()
