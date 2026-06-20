"""队友轮询与回禀处理"""
import time

from ..config import TIMEOUTS
from ..core.runtime_types import InboxMessageDict
from ..logger import logger
from ..utils import now_ts

REPLY_INSTRUCTION = "队友回禀已收到。先 blackboard_read 读黑板结果，再回复。"

def format_inbox_messages(inbox: list[InboxMessageDict]) -> str | None:
    """将 inbox 消息列表格式化为回禀文本，过滤 shutdown_response。"""
    parts = []
    for im in inbox:
        if im.get("type") == "shutdown_response":
            continue
        sender = im.get("from", "?")
        content = im.get("content", "")
        parts.append(f"[{sender} 回禀]\n{content}")
    return "\n\n".join(parts) if parts else None

def poll_inbox(bus, name="lead"):
    msgs = bus.read_inbox(name)
    if not msgs:
        return None
    result = format_inbox_messages(msgs)
    if result:
        logger.info(f"[回禀←] {name} 收到回禀")
    return result

def has_active_teammates(team_mgr):
    return team_mgr.has_working_members()

def wait_for_teammates(bus, team_mgr, lead_event, run_loop_fn, messages, tools, inject_fn, disp, history_db=None, ctx=None, workspace="default", session_id=""):
    from datetime import datetime, timezone, timedelta
    
    # run_tool_loop 内部每轮已检查 inbox 并注入回禀，这里只等队友完成 + 处理 loop 退出后到达的消息
    if not has_active_teammates(team_mgr):
        return None

    logger.info("[等待] 队友工作中...")
    disp.info("⏳ 队友工作中，等待回禀...")
    waited = 0
    last_msg = None
    poll_interval = TIMEOUTS.get("lead_poll_interval", 2)

    while waited < TIMEOUTS.get("lead_wait", 1800):
        if not has_active_teammates(team_mgr):
            break
        lead_event.clear()
        lead_event.wait(timeout=poll_interval)
        waited += poll_interval

        # 检查 run_tool_loop 可能没消费到的回禀
        inbox_text = poll_inbox(bus)
        if inbox_text:
            disp.info("📬 收到队友回禀")
            _ts = now_ts()
            messages.append({"role": "user", "content": inbox_text, "timestamp": _ts})
            messages.append({"role": "user", "content": REPLY_INSTRUCTION, "timestamp": _ts})
            if history_db:
                history_db.append(workspace, session_id, "user", inbox_text)
            last_msg = run_loop_fn(messages, tools, inject_fn, disp, ctx=ctx)
            waited = 0

    # 最终检查：队友刚完成时回禀可能还在 inbox
    final = poll_inbox(bus)
    if final:
        disp.info("📬 收到队友回禀")
        _ts = now_ts()
        messages.append({"role": "user", "content": final, "timestamp": _ts})
        messages.append({"role": "user", "content": REPLY_INSTRUCTION, "timestamp": _ts})
        if history_db:
            history_db.append(workspace, session_id, "user", final)
        last_msg = run_loop_fn(messages, tools, inject_fn, disp, ctx=ctx)

    if not has_active_teammates(team_mgr):
        logger.info("[等待] 所有队友已完成，退出等待")
    else:
        logger.warning(f"[等待] 超时 ({TIMEOUTS.get('lead_wait', 1800)}s)，强制退出等待")

    return last_msg

def shutdown_teammates(bus, team_mgr):
    targets = team_mgr.active_member_names()
    if targets:
        logger.info(f"[自动shutdown] {len(targets)} 位队友: {', '.join(targets)}")
        for name in targets:
            bus.send("lead", name, "任务结束，请退出。", "shutdown_request")

def cleanup_inbox(bus, delay=0.5):
    """清理 lead inbox 残留消息（shutdown_response 等）。
    delay 给队友线程一点时间把最后消息写盘。正常回禀已在 wait_for_teammates 中消费。"""
    time.sleep(delay)
    leftover = bus.read_inbox("lead")
    if leftover:
        logger.debug(f"[清理] 丢弃 {len(leftover)} 条残留 inbox 消息")
