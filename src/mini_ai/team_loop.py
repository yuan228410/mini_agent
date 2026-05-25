"""队友轮询与回禀处理"""
import time

from .config import TIMEOUTS
from .logger import logger


def poll_inbox(bus, name="lead"):
    msgs = bus.read_inbox(name)
    if not msgs:
        return None
    parts = []
    for m in msgs:
        if m.get("type") == "shutdown_response":
            logger.info(f"[回禀←] {m.get('from', '?')} 已退出，忽略")
            continue
        sender = m.get("from", "?")
        content = m.get("content", "")
        if len(content) < 30 and not any(kw in content for kw in ("完成", "结果", "修复", "审查", "报告", "Error")):
            continue
        parts.append(f"[{sender} 回禀]\n{content}")
    if not parts:
        return None
    logger.info(f"[回禀←] {name} 收到 {len(parts)} 条消息")
    return "\n\n".join(parts)


def has_active_teammates(team_mgr):
    with team_mgr.lock:
        return any(m["status"] == "working" for m in team_mgr.config.get("members", []))


def wait_for_teammates(bus, team_mgr, lead_event, run_loop_fn, messages, tools, inject_fn, disp, store):
    if not has_active_teammates(team_mgr):
        return None

    logger.info("[等待] 队友工作中...")
    disp.info("⏳ 队友工作中，等待回禀...")
    waited = 0
    last_msg = None

    while waited < TIMEOUTS["lead_wait"]:
        lead_event.clear()
        lead_event.wait(timeout=TIMEOUTS["lead_poll_interval"])
        waited += TIMEOUTS["lead_poll_interval"]

        inbox_text = poll_inbox(bus)
        if inbox_text:
            disp.info("📬 收到队友回禀")
            messages.append({"role": "user", "content": inbox_text})
            store.append("user", inbox_text)
            last_msg = run_loop_fn(messages, tools, inject_fn, disp)

        if not has_active_teammates(team_mgr):
            break
        if inbox_text:
            waited = 0

    if not has_active_teammates(team_mgr):
        logger.info("[等待] 所有队友已完成，退出等待")
    else:
        logger.warning(f"[等待] 超时 ({TIMEOUTS["lead_wait"]}s)，强制退出等待")

    return last_msg


def shutdown_teammates(bus, team_mgr):
    targets = []
    with team_mgr.lock:
        for m in team_mgr.config.get("members", []):
            if m["status"] in ("idle", "working"):
                targets.append(m["name"])
    if targets:
        logger.info(f"[自动shutdown] {len(targets)} 位队友: {', '.join(targets)}")
        for name in targets:
            bus.send("lead", name, "任务结束，请退出。", "shutdown_request")


def cleanup_inbox(bus, delay=0.5):
    time.sleep(delay)
    leftover = bus.read_inbox("lead")
    if leftover:
        logger.debug(f"[清理] 丢弃 {len(leftover)} 条残留 inbox 消息")
