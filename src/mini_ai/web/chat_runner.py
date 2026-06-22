"""Web 端工具循环运行器 — 从 chat.py 提取的 _run_tool_loop_sync"""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..llm import get_usage
from ..application.chat_service import ApplicationService, RunTurnOptions, build_chat_runtime_bundle, finalize_chat_assistant_response, handle_invalid_chat_result, inject_team_inbox_messages, prepare_chat_turn, should_poll_team_followup, team_followup_timing, valid_assistant_result
from ..application.session_service import maybe_auto_name_session
from ..core.events import DisplayEvent, DisplayEventType
from ..core.runtime_types import MessageDict, ToolDefinition
from ..logger import logger, set_session_id
from .display import WebDisplay
from .queue_utils import safe_queue_put
from .session_manager import (
    SessionManager, cache_key,
    resolve_base, get_or_create_components,
    _save_session_name, _update_meta_cache,
)
from .runtime_helpers import chat_runtime_dependencies

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

                disp = WebDisplay(queue, loop, session_id=comp_key, suppress_text=plan_turn)
                runtime = build_chat_runtime_bundle(
                    chat_runtime_dependencies(),
                    username=username,
                    workspace=workspace,
                    session_id=comp_key,
                    base=base,
                    messages=messages,
                    display=disp,
                    components=comp,
                    abort_event=abort_event,
                    model_name=model_name,
                ).runtime
                disp._usage_provider = runtime.usage_collector.snapshot if runtime.usage_collector else None
                if runtime.execution_budget:
                    disp._stream_flush_ms = runtime.execution_budget.stream_chunk_flush_ms
                    disp._stream_max_chars = runtime.execution_budget.stream_chunk_max_chars
                ctx = runtime.request_context
                settings = runtime.settings

                prepared = prepare_chat_turn(
                    runtime=runtime,
                    messages=messages,
                    tools=tools,
                    history_db=comp["history_db"],
                    workspace=workspace or "default",
                    session_id=comp_key,
                    session_key=session_key,
                    plan_state_store=sm,
                    plan_turn=plan_turn,
                    approved_plan=approved_plan,
                    max_turns=max_turns,
                    abort_event=abort_event,
                    display=disp,
                )
                tools = prepared.tools

                maybe_auto_name_session(messages, base=base, session_id=comp_key, save_session_name=_save_session_name)

                logger.debug(f"[Web] run_tool_loop start key={session_key} plan_turn={plan_turn} tools={len(tools)}")

                result = ApplicationService().run_turn(
                    runtime=runtime,
                    tools=tools,
                    plan_store=prepared.plan_store,
                    plan_state=sm,
                    user_text_for_history=prepared.user_text_for_history,
                    options=prepared.options,
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
                if should_poll_team_followup(bus, team_mgr, msg):
                    lead_event = threading.Event()
                    timing = team_followup_timing(settings.timeouts if settings else None, now=time.monotonic())

                    while time.monotonic() < timing.deadline:
                        inbox = bus.read_inbox("lead")
                        if inbox:
                            injection = inject_team_inbox_messages(messages, inbox)
                            if injection.injected:
                                logger.info(f"[Web/队友] 兜底注入 {injection.count} 条回禀")
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
                        lead_event.wait(timeout=timing.poll_interval)

                # ── 错误处理 ──
                if not valid_assistant_result(msg):
                    if msg is None:
                        logger.error(f"[Web⚠] msg=None, 可能是流式错误或中断")
                    usage = dict(get_usage())
                    error_result = handle_invalid_chat_result(
                        message=msg,
                        messages=messages,
                        history_db=comp["history_db"],
                        workspace=workspace or "default",
                        history_session_id=comp_key,
                        event_session_id=session_key,
                        usage=usage,
                    )
                    logger.warning(f"[Web] error path: err={error_result.error_text[:80]}")
                    safe_queue_put(queue, error_result.event, loop)
                    return error_result.message, {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}

                # ── 正常流程 ──
                dispatch = finalize_chat_assistant_response(messages=messages, result=result, plan_turn=plan_turn)
                if dispatch.display_content is not None:
                    safe_queue_put(queue, _ws_event(DisplayEventType.TEXT, content=dispatch.display_content, session_id=comp_key), loop)

                usage = dispatch.usage
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
