"""Thread-safe Web queue helpers."""
from __future__ import annotations

from ..core.events import TERMINAL_EVENT_TYPES

TERMINAL_EVENT_NAMES = {event.value for event in TERMINAL_EVENT_TYPES}


def safe_queue_put(queue, item, loop=None) -> bool:
    """线程安全投递队列事件。

    终止类事件会尽量腾出一个普通事件槽位；返回 bool 表示是否成功投递。
    """

    result = {"ok": False}

    def _put():
        try:
            queue.put_nowait(item)
            result["ok"] = True
            return
        except Exception:
            pass
        if item.get("event") in TERMINAL_EVENT_NAMES:
            try:
                buffered = []
                dropped = False
                while True:
                    old = queue.get_nowait()
                    if not dropped and old.get("event") not in TERMINAL_EVENT_NAMES:
                        dropped = True
                        continue
                    buffered.append(old)
            except Exception:
                pass
            for old in buffered:
                try:
                    queue.put_nowait(old)
                except Exception:
                    break
            try:
                queue.put_nowait(item)
                result["ok"] = True
            except Exception:
                result["ok"] = False

    if loop is not None:
        try:
            loop.call_soon_threadsafe(_put)
        except Exception:
            return False
        return True

    _put()
    return result["ok"]
