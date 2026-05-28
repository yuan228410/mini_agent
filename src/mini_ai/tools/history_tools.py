"""历史搜索工具 — 跨会话全文检索"""
import threading

from ..logger import logger

_history_db = threading.local()


def configure(history_db=None):
    if history_db is not None:
        _history_db.store = history_db


def _get_db():
    try:
        return _history_db.store
    except AttributeError:
        return None


_search_def = {
    "type": "function",
    "function": {
        "name": "search_history",
        "description": (
            "搜索历史对话记录。当需要回顾之前的讨论、查找过去的决策或补充上下文信息时使用。"
            "支持关键词全文搜索和日期范围过滤。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "date_from": {"type": "string", "description": "起始日期，格式 YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "最大返回条数，默认 20"},
            },
            "required": ["keyword"],
        },
    },
}


def _search_exec(args: dict) -> str:
    db = _get_db()
    if not db:
        return "Error: 历史数据库未初始化"

    keyword = args.get("keyword", "")
    date_from = args.get("date_from", "")
    date_to = args.get("date_to", "")
    limit = args.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20

    results = db.search(keyword, date_from=date_from, date_to=date_to, limit=limit)

    if not results:
        return f"未找到包含 '{keyword}' 的历史记录"

    lines = [f"找到 {len(results)} 条历史记录:"]
    for r in results:
        ts = r["ts"][:16]
        role = r["role"]
        content = r["content"][:200]
        lines.append(f"  [{ts}] {role}: {content}")

    return "\n".join(lines)


class _ToolMod:
    def __init__(self, definition, execute):
        self.definition = definition
        self.execute = execute


search_history_mod = _ToolMod(_search_def, _search_exec)

_manage_def = {
    "type": "function",
    "function": {
        "name": "manage_history",
        "description": (
            "管理历史消息：查看、清理、删除。"
            "支持：列出消息概览、保留最近N条删除旧消息、按关键词查找并删除匹配消息、彻底删除所有消息。"
            "重要：confirmed=false 时只预览不删除，必须将预览结果展示给用户并等待用户明确确认后，才能传 confirmed=true 执行。"
            "绝对不能自行传 confirmed=true，必须由用户确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "keep_recent", "delete_keyword", "delete_all"],
                    "description": (
                        "list=列出消息概览，"
                        "keep_recent=保留最近N条删除其余，"
                        "delete_keyword=删除包含关键词的消息，"
                        "delete_all=彻底删除所有消息"
                    ),
                },
                "keep_count": {
                    "type": "integer",
                    "description": "keep_recent 时保留的最近消息条数",
                },
                "keyword": {
                    "type": "string",
                    "description": "delete_keyword 时匹配的关键词",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "用户确认执行删除，默认 false（先预览）",
                },
                "batch_size": {
                    "type": "integer",
                    "description": "分批删除时每批数量，默认 200",
                },
            },
            "required": ["action"],
        },
    },
}

def _manage_exec(args: dict) -> str:
    db = _get_db()
    if not db:
        return "Error: 历史数据库未初始化"

    action = args.get("action", "list")
    confirmed = args.get("confirmed", False)

    if action == "list":
        msgs = db.list_for_review()
        if not msgs:
            return "没有历史消息"
        lines = [f"共 {len(msgs)} 条消息："]
        for m in msgs:
            ts = m["ts"][:16] if m["ts"] else ""
            content = m["content"][:80].replace("\n", " ")
            lines.append(f"  [{m['id']}] [{ts}] {m['role']}: {content}")
        return "\n".join(lines)

    if action == "keep_recent":
        keep_count = args.get("keep_count", 50)
        try:
            keep_count = int(keep_count)
        except (TypeError, ValueError):
            return "keep_count 必须是整数"
        msgs = db.list_for_review()
        total = len(msgs)
        if total <= keep_count:
            return f"当前 {total} 条消息，无需清理（保留 {keep_count} 条）"
        to_delete = total - keep_count
        batch_size = args.get("batch_size", 200)
        if not confirmed:
            preview = msgs[:3]
            lines = [f"将删除 {to_delete} 条旧消息，保留最近 {keep_count} 条。待删除的前 3 条："]
            for m in preview:
                content = m["content"][:60].replace("\n", " ")
                lines.append(f"  [{m['id']}] {m['role']}: {content}")
            lines.append("... 请用 confirmed=true 确认执行")
            return "\n".join(lines)
        total_deleted = 0
        remaining = to_delete
        while remaining > 0:
            batch = min(batch_size, remaining)
            db.delete_before(keep_count + remaining - batch)
            total_deleted += batch
            remaining -= batch
        return f"已删除 {total_deleted} 条旧消息，保留最近 {keep_count} 条"

    if action == "delete_keyword":
        keyword = args.get("keyword", "")
        if not keyword:
            return "请指定 keyword 参数"
        results = db.search(keyword, limit=100)
        if not results:
            return f"未找到包含 '{keyword}' 的消息"
        ids = [r["id"] for r in results if "id" in r]
        if not ids:
            search_by_kw = db.list_for_review()
            ids = [m["id"] for m in search_by_kw if keyword.lower() in m["content"].lower()]
            results = [{"id": m["id"], "role": m["role"], "content": m["content"][:60]} for m in search_by_kw if keyword.lower() in m["content"].lower()]
        if not ids:
            return f"未找到包含 '{keyword}' 的消息"
        if not confirmed:
            lines = [f"找到 {len(ids)} 条包含 '{keyword}' 的消息："]
            for r in results[:5]:
                content = (r.get("content") or "")[:60].replace("\n", " ")
                lines.append(f"  [{r.get('id', '?')}] {r.get('role', '?')}: {content}")
            if len(ids) > 5:
                lines.append(f"  ... 还有 {len(ids) - 5} 条")
            lines.append("请用 confirmed=true 确认执行")
            return "\n".join(lines)
        deleted = db.delete_by_ids(ids)
        return f"已删除 {deleted} 条包含 '{keyword}' 的消息"

    if action == "delete_all":
        msgs = db.list_for_review()
        total = len(msgs)
        if total == 0:
            return "没有历史消息需要删除"
        if not confirmed:
            return f"将彻底删除所有 {total} 条历史消息（不可恢复）。请用 confirmed=true 确认执行"
        batch_size = args.get("batch_size", 200)
        total_deleted = 0
        while True:
            msgs = db.list_for_review()
            if not msgs:
                break
            batch_ids = [m["id"] for m in msgs[:batch_size]]
            db.delete_by_ids(batch_ids)
            total_deleted += len(batch_ids)
            if len(msgs) <= batch_size:
                break
        return f"已彻底删除所有 {total_deleted} 条历史消息"

    return f"未知 action: {action}"

manage_history_mod = _ToolMod(_manage_def, _manage_exec)
ALL_HISTORY_TOOLS = [search_history_mod, manage_history_mod]
