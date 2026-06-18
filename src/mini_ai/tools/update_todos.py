"""待办列表工具 — 跨压缩存活的计划管理，per-session 隔离"""
import contextvars
import threading

from ..logger import logger

_VALID_STATUS = ("pending", "in_progress", "completed")
_ICONS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}

_current_session = contextvars.ContextVar("todo_session", default="default")

definition = {
    "type": "function",
    "function": {
        "name": "update_todos",
        "description": "更新待办列表。每次传入完整列表（全量覆盖）。并行任务最多 5 个 in_progress。",
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "待办列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "序号（从1开始）"},
                            "content": {"type": "string", "description": "任务内容"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}
                        },
                        "required": ["id", "content", "status"]
                    }
                }
            },
            "required": ["todos"]
        }
    }
}


class TodoStore:
    """跨回合存活的待办列表，不在消息历史中，压缩不会丢失"""

    def __init__(self):
        self.items: list[dict] = []
        self._lock = threading.Lock()

    def update(self, todos: list[dict]) -> str:
        cleaned = []
        for i, t in enumerate(todos, start=1):
            content = (t.get("content") or "").strip()
            if not content:
                continue
            status = t.get("status", "pending")
            if status not in _VALID_STATUS:
                status = "pending"
            cleaned.append({"id": t.get("id", i), "content": content, "status": status})

        in_progress = sum(1 for t in cleaned if t["status"] == "in_progress")
        if in_progress > 5:
            return "Error: in_progress 任务过多（最多 5 个并行）。"

        with self._lock:
            self.items = cleaned
        return self.render()

    def render(self) -> str:
        with self._lock:
            items = list(self.items)
        if not items:
            return ""
        lines = []
        for t in items:
            icon = _ICONS.get(t["status"], "[?]")
            if t["status"] == "in_progress":
                lines.append(f"{icon} **{t['id']}. {t['content']}** ← 当前")
            else:
                lines.append(f"{icon} {t['id']}. {t['content']}")
        return "📋TODO\n" + "\n".join(lines)


_stores_lock = threading.Lock()
_stores: dict[str, TodoStore] = {}


def _get_store() -> TodoStore:
    sid = _current_session.get()
    with _stores_lock:
        if sid not in _stores:
            _stores[sid] = TodoStore()
        return _stores[sid]


# 兼容：CLI 模式和 render_todos() 使用
_store = type("_StoreProxy", (), {
    "render": staticmethod(lambda: _get_store().render()),
    "update": staticmethod(lambda todos: _get_store().update(todos)),
})()


def set_session(session_id: str):
    _current_session.set(session_id)


def get_todos(session_id: str | None = None) -> list[dict]:
    """获取指定会话的 todos，未指定则使用当前会话"""
    if session_id:
        with _stores_lock:
            store = _stores.get(session_id)
            return list(store.items) if store else []
    else:
        return list(_get_store().items)


def set_todos(session_id: str, todos: list[dict]) -> str:
    """直接设置指定会话的 todos，供计划审批/执行入口快速初始化。"""
    with _stores_lock:
        if session_id not in _stores:
            _stores[session_id] = TodoStore()
        store = _stores[session_id]
    return store.update(todos)


def execute(args: dict) -> str:
    todos = args.get("todos", [])
    if not todos:
        return "Error: 缺少 todos 参数"
    lines = []
    for t in todos:
        icon = _ICONS.get(t.get("status", "pending"), "[?]")
        lines.append(f"  {icon} {t.get('id')}. {t.get('content', '')}  [{t.get('status', '?')}]")
    logger.info("[计划]\n" + "\n".join(lines))

    result = _store.update(todos)
    return result

def cleanup_session(sid: str):
    with _stores_lock:
        _stores.pop(sid, None)


def inject_todos(messages: list[dict]):
    """将当前任务计划注入 system prompt 的尾部（供 main.py 和 chat.py 共用）"""
    from . import render_todos
    todos_text = render_todos()
    base = messages[0]["content"]
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[:base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"
