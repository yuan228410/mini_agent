"""待办列表工具 — 跨压缩存活的计划管理，per-session 隔离"""
from __future__ import annotations

import contextvars
import threading
from typing import Literal, Mapping, TypedDict

from ..core.runtime_types import MessageDict, ToolArgs, ToolDefinition

from ..logger import logger

TodoStatus = Literal["pending", "in_progress", "completed"]


class TodoItem(TypedDict):
    id: int
    content: str
    status: TodoStatus


TodoInput = Mapping[str, object]

_VALID_STATUS: tuple[TodoStatus, ...] = ("pending", "in_progress", "completed")
_ICONS: dict[TodoStatus, str] = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}

_current_session = contextvars.ContextVar("todo_session", default="default")

definition: ToolDefinition = {
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
        self.items: list[TodoItem] = []
        self._lock = threading.Lock()

    def update(self, todos: list[TodoInput]) -> str:
        cleaned: list[TodoItem] = []
        for i, t in enumerate(todos, start=1):
            content = str(t.get("content") or "").strip()
            if not content:
                continue
            raw_status = t.get("status", "pending")
            status: TodoStatus = raw_status if raw_status in _VALID_STATUS else "pending"
            raw_id = t.get("id", i)
            todo_id = raw_id if isinstance(raw_id, int) else i
            cleaned.append({"id": todo_id, "content": content, "status": status})

        in_progress = sum(1 for t in cleaned if t["status"] == "in_progress")
        if in_progress > 5:
            return "Error: in_progress 任务过多（最多 5 个并行）。"

        with self._lock:
            self.items = cleaned
        return self.render()

    def render(self) -> str:
        return self.render_body()

    def render_body(self) -> str:
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
        return "\n".join(lines)


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
    "render_body": staticmethod(lambda: _get_store().render_body()),
    "update": staticmethod(lambda todos: _get_store().update(todos)),
})()


def set_session(session_id: str):
    _current_session.set(session_id)


def get_todos(session_id: str | None = None) -> list[TodoItem]:
    """获取指定会话的 todos，未指定则使用当前会话"""
    if session_id:
        with _stores_lock:
            store = _stores.get(session_id)
            return list(store.items) if store else []
    else:
        return list(_get_store().items)


def render_current_todos() -> str:
    """Render todos for the current context-bound session without touching ToolRegistry."""
    return _get_store().render()


def render_current_todo_body() -> str:
    """Render todos without transport/UI sentinels."""
    return _get_store().render_body()


def set_todos(session_id: str, todos: list[TodoInput]) -> str:
    """直接设置指定会话的 todos，供计划审批/执行入口快速初始化。"""
    with _stores_lock:
        if session_id not in _stores:
            _stores[session_id] = TodoStore()
        store = _stores[session_id]
    return store.update(todos)


def _todo_inputs(args: ToolArgs) -> list[TodoInput]:
    raw = args.get("todos", [])
    if not isinstance(raw, list):
        return []
    return [t for t in raw if isinstance(t, Mapping)]


def execute(args: ToolArgs) -> str:
    todos = _todo_inputs(args)
    if not todos:
        return "Error: 缺少 todos 参数"
    lines = []
    for t in todos:
        raw_status = t.get("status", "pending")
        status: TodoStatus = raw_status if raw_status in _VALID_STATUS else "pending"
        icon = _ICONS.get(status, "[?]")
        lines.append(f"  {icon} {t.get('id')}. {t.get('content', '')}  [{status}]")
    logger.info("[计划]\n" + "\n".join(lines))

    result = _store.update(todos)
    return result

def cleanup_session(sid: str):
    with _stores_lock:
        _stores.pop(sid, None)


def inject_todos(messages: list[MessageDict]) -> None:
    """将当前任务计划注入 system prompt 的尾部（供 main.py 和 chat.py 共用）"""
    todos_text = render_current_todos()
    base = str(messages[0]["content"])
    marker = "\n\n## 当前任务计划"
    if marker in base:
        base = base[:base.index(marker)]
    messages[0]["content"] = base + f"{marker}\n\n{todos_text}"
