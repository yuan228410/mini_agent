"""主动记忆工具 — Agent 实时写入/读取长期记忆"""
import contextvars

from ..logger import logger

_memory_store = contextvars.ContextVar("memory_store", default=None)


def configure(memory_store=None):
    if memory_store is not None:
        _memory_store.set(memory_store)


def _get_store():
    return _memory_store.get()


# ── remember ──

_remember_def = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "将重要信息主动写入长期记忆。适用于：用户偏好、关键决策、项目背景、重要发现等"
            "需要跨对话保留的信息。记忆会持久保存，后续对话可自动参考。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的信息内容，简洁但具体",
                },
                "category": {
                    "type": "string",
                    "enum": ["user_preference", "project_info", "decision", "discovery", "general"],
                    "description": "记忆分类",
                },
                "level": {
                    "type": "string",
                    "enum": ["global", "user", "workspace"],
                    "description": "记忆写入层级：global（全局）、user（用户级，默认）、workspace（工作空间级）",
                },
            },
            "required": ["content"],
        },
    },
}


def _remember_exec(args: dict) -> str:
    store = _get_store()
    if not store:
        return "Error: 记忆系统未初始化"
    content = args.get("content", "").strip()
    if not content:
        return "Error: content 不能为空"
    category = args.get("category", "general")
    level = args.get("level", "user")

    tier_dir = store.get_tier_dir(level)
    if not tier_dir:
        return f"Error: 层级 '{level}' 不可用"

    target_file = tier_dir / "MEMORY.md"
    current = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    entry = f"- [{category}] {content}"

    if entry in current:
        return "已存在相同记忆，跳过"

    new_memory = current.rstrip() + "\n" + entry + "\n" if current else entry + "\n"
    store.write_memory_at(new_memory, level)
    logger.info(f"[记忆+] [{category}] {content[:60]} level={level}")
    return f"已记住({level}): {content}"


# ── recall ──

_recall_def = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "从长期记忆中检索信息。可按关键词过滤。"
            "长期记忆包含用户偏好、项目背景、历史决策等跨对话持久信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "可选关键词过滤，不填返回全部记忆",
                },
            },
        },
    },
}


def _recall_exec(args: dict) -> str:
    store = _get_store()
    if not store:
        return "Error: 记忆系统未初始化"
    keyword = args.get("keyword", "").strip()
    memory = store.read_memory()
    if not memory:
        return "长期记忆为空"

    if keyword:
        lines = [l for l in memory.splitlines() if keyword.lower() in l.lower()]
        if not lines:
            return f"未找到包含 '{keyword}' 的记忆"
        return "\n".join(lines)

    return memory


# ── forget ──

_forget_def = {
    "type": "function",
    "function": {
        "name": "forget",
        "description": "从长期记忆中删除包含指定关键词的条目。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要删除的记忆中包含的关键词",
                },
                "level": {
                    "type": "string",
                    "enum": ["global", "user", "workspace"],
                    "description": "记忆删除层级：global（全局）、user（用户级，默认）、workspace（工作空间级）",
                },
            },
            "required": ["keyword"],
        },
    },
}


def _forget_exec(args: dict) -> str:
    store = _get_store()
    if not store:
        return "Error: 记忆系统未初始化"
    keyword = args.get("keyword", "").strip()
    if not keyword:
        return "Error: keyword 不能为空"
    level = args.get("level", "user")

    tier_dir = store.get_tier_dir(level)
    if not tier_dir:
        return f"Error: 层级 '{level}' 不可用"

    target_file = tier_dir / "MEMORY.md"
    memory = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    if not memory:
        return "长期记忆为空，无需删除"

    lines = memory.splitlines()
    remaining = [l for l in lines if keyword.lower() not in l.lower()]
    removed = len(lines) - len(remaining)

    if removed == 0:
        return f"未找到包含 '{keyword}' 的记忆"

    store.write_memory_at("\n".join(remaining) + "\n" if remaining else "", level)
    logger.info(f"[记忆-] 删除 {removed} 条包含 '{keyword}' 的记忆 level={level}")
    return f"已删除 {removed} 条记忆({level})"


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition, execute):
        self.definition = definition
        self.execute = execute


remember_mod = _ToolMod(_remember_def, _remember_exec)
recall_mod = _ToolMod(_recall_def, _recall_exec)
forget_mod = _ToolMod(_forget_def, _forget_exec)

ALL_MEMORY_TOOLS = [remember_mod, recall_mod, forget_mod]
