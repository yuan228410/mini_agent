"""历史搜索工具 — 跨会话全文检索"""
from ..logger import logger

_history_db = None


def configure(history_db=None):
    global _history_db
    if history_db is not None:
        _history_db = history_db


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
    if not _history_db:
        return "Error: 历史数据库未初始化"

    keyword = args.get("keyword", "")
    date_from = args.get("date_from", "")
    date_to = args.get("date_to", "")
    limit = args.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20

    results = _history_db.search(keyword, date_from=date_from, date_to=date_to, limit=limit)

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

ALL_HISTORY_TOOLS = [search_history_mod]
