"""历史搜索工具 — 跨会话全文检索"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import json
import re

from ..logger import logger


def _compact_message(msg: dict) -> dict:
    """压缩单条消息，保留关键信息，过滤冗余
    
    处理内容：
    - tool 消息：截断长结果
    - tool_calls：简化参数，只保留关键参数
    - thinking：过滤掉
    - 图片：保留摘要信息
    """
    role = msg.get("role", "")
    content = msg.get("content", "")
    ts = msg.get("ts", "")
    
    # 1. tool 消息：截断长结果
    if role == "tool":
        if isinstance(content, str) and len(content) > 300:
            return {
                "role": role,
                "content": content[:300] + f"\n... [共 {len(content)} 字已截断]",
                "ts": ts
            }
        return {"role": role, "content": content, "ts": ts}
    
    # 2. assistant 消息含 tool_calls
    if msg.get("tool_calls"):
        calls_summary = []
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "{}")
            try:
                args = json.loads(raw_args) if raw_args else {}
                # 保留关键参数
                key_params = {
                    k: v for k, v in args.items()
                    if k in ("path", "keyword", "url", "command", "query", 
                            "pattern", "name", "content", "cwd", "action") 
                    and v is not None and v != ""
                }
                if key_params:
                    args_str = json.dumps(key_params, ensure_ascii=False)
                    if len(args_str) > 80:
                        args_str = args_str[:77] + "..."
                    calls_summary.append(f"{name}({args_str})")
                else:
                    calls_summary.append(name)
            except (json.JSONDecodeError, TypeError):
                calls_summary.append(name)
        return {
            "role": role,
            "content": f"[调用工具] {', '.join(calls_summary)}",
            "ts": ts
        }
    
    # 3. 过滤 thinking 标签
    if isinstance(content, str):
        # 移除 <thinking>...</thinking>
        content = re.sub(r'<thinking>.*?</thinking>', '', content, flags=re.DOTALL)
        # 移除 "思考：..." 段落
        content = re.sub(r'\n*思考[：:].{0,200}\n', '\n', content)
        content = content.strip()
    
    # 4. 图片信息摘要
    if isinstance(content, list):
        parts = []
        img_count = 0
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if text:
                        parts.append(text)
                elif item.get("type") == "image_url":
                    img_count += 1
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith(("http://", "https://")):
                        url_preview = url if len(url) <= 60 else url[:57] + "..."
                        parts.append(f"[图片: {url_preview}]")
                    else:
                        parts.append(f"[图片{img_count}]")
        return {"role": role, "content": "\n".join(parts), "ts": ts}
    
    return {"role": role, "content": content, "ts": ts}


_search_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "search_history",
        "description": "搜索历史对话。支持关键词和日期过滤。默认压缩模式（compact=true）。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "date_from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "最大返回条数，默认 20"},
                "compact": {"type": "boolean", "description": "压缩返回，默认 true"},
            },
            "required": [],
        },
    },
}


def search_history_with_db(db, workspace: str, args: ToolArgs) -> str:
    if not db:
        return "Error: 历史数据库未初始化"

    keyword = args.get("keyword", "")
    date_from = args.get("date_from", "")
    date_to = args.get("date_to", "")
    limit = args.get("limit", 20)
    compact = args.get("compact", True)  # 默认压缩
    
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20

    # 查询总数
    total = db.count(workspace=workspace, keyword=keyword, date_from=date_from, date_to=date_to)
    
    if total == 0:
        return f"未找到符合条件的历史记录"

    # 查询结果
    results = db.search(keyword=keyword, workspace=workspace, date_from=date_from, date_to=date_to, limit=limit)
    
    # 压缩处理
    if compact:
        results = [_compact_message(r) for r in results]

    # 格式化输出
    lines = [f"找到 {total} 条记录，返回前 {len(results)} 条:"]
    
    # 提示搜索模式
    if keyword and not db.is_fts_available():
        lines.append("⚠️ FTS5 不可用，使用模糊匹配（LIKE）模式")
    
    for r in results:
        ts = (r.get("ts") or "")[:16]
        role = r.get("role", "?")
        content = (r.get("content") or "")[:200].replace("\n", " ")
        lines.append(f"  [{ts}] {role}: {content}")
    
    # 提示遗漏
    if total > limit:
        lines.append(f"\n⚠️ 还有 {total - limit} 条未显示，可加大 limit 查看")

    return "\n".join(lines)


class _ToolMod:
    def __init__(self, definition: ToolDefinition, execute):
        self.definition = definition
        self.execute = execute


def _search_exec(args: ToolArgs) -> str:
    return "Error: 历史数据库未初始化"


search_history_mod = _ToolMod(_search_def, _search_exec)

_manage_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "manage_history",
        "description": "管理历史消息。confirmed=false 只预览，必须用户确认后才能 confirmed=true。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "keep_recent", "delete_keyword", "delete_all"],
                    "description": "list/keep_recent/delete_keyword/delete_all",
                },
                "keep_count": {"type": "integer", "description": "keep_recent 时保留条数"},
                "keyword": {"type": "string", "description": "delete_keyword 时匹配关键词"},
                "confirmed": {"type": "boolean", "description": "用户确认执行"},
                "batch_size": {"type": "integer", "description": "分批删除每批数量"},
            },
            "required": ["action"],
        },
    },
}

def manage_history_with_db(db, workspace: str, args: ToolArgs) -> str:
    if not db:
        return "Error: 历史数据库未初始化"

    action = args.get("action", "list")
    confirmed = args.get("confirmed", False)

    if action == "list":
        msgs = db.list_for_review(workspace)
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
        msgs = db.list_for_review(workspace)
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
        total_deleted = db.delete_before(workspace, keep_count)
        return f"已删除 {total_deleted} 条旧消息，保留最近 {keep_count} 条"

    if action == "delete_keyword":
        keyword = args.get("keyword", "")
        if not keyword:
            return "请指定 keyword 参数"
        results = db.search(keyword=keyword, workspace=workspace, limit=1000)
        if not results:
            return f"未找到包含 '{keyword}' 的消息"
        ids = [r["id"] for r in results if "id" in r]
        if not ids:
            all_msgs = db.list_for_review(workspace, limit=1000)
            ids = [m["id"] for m in all_msgs if keyword.lower() in m["content"].lower()]
            results = [{"id": m["id"], "role": m["role"], "content": m["content"][:60]} for m in all_msgs if keyword.lower() in m["content"].lower()]
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
        msgs = db.list_for_review(workspace)
        total = len(msgs)
        if total == 0:
            return "没有历史消息需要删除"
        if not confirmed:
            return f"将彻底删除所有 {total} 条历史消息（不可恢复）。请用 confirmed=true 确认执行"
        total_deleted = db.purge(workspace)
        return f"已彻底删除所有 {total_deleted} 条历史消息"

    return f"未知 action: {action}"


def _manage_exec(args: ToolArgs) -> str:
    return "Error: 历史数据库未初始化"


manage_history_mod = _ToolMod(_manage_def, _manage_exec)
ALL_HISTORY_TOOLS = [search_history_mod, manage_history_mod]
