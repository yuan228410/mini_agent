"""主动记忆工具 — Agent 实时写入/读取长期记忆"""
from ..core.runtime_types import ToolArgs, ToolDefinition

from ..logger import logger


# ── remember ──

_remember_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "写入长期记忆。适用于用户偏好、关键决策、项目背景等跨对话保留的信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的内容"},
                "category": {"type": "string", "enum": ["user_preference", "project_info", "decision", "discovery", "general"], "description": "记忆分类"},
                "level": {"type": "string", "enum": ["global", "user", "workspace"], "description": "层级：global/user/workspace，默认 user"},
            },
            "required": ["content"],
        },
    },
}


def remember_with_store(store, args: ToolArgs) -> str:
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

_recall_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "检索长期记忆。可按关键词过滤，不传 keyword 返回全部。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "关键词过滤"},
            },
        },
    },
}


def recall_with_store(store, args: ToolArgs) -> str:
    if not store:
        return "Error: 记忆系统未初始化"
    keyword = args.get("keyword", "").strip()
    
    if not keyword:
        # 无关键词，返回全部记忆
        memory = store.read_memory()
        return memory if memory else "长期记忆为空"
    
    # 有关键词，逐行过滤（避免读取整个大文件）
    tier_paths = store._tier_paths() if hasattr(store, '_tier_paths') else []
    if not tier_paths:
        # 降级：使用原方法
        memory = store.read_memory()
        if not memory:
            return "长期记忆为空"
        lines = [l for l in memory.splitlines() if keyword.lower() in l.lower()]
        if not lines:
            return f"未找到包含 '{keyword}' 的记忆"
        return "\n".join(lines)
    
    # 逐层读取并过滤
    matched_lines = []
    seen = set()  # 去重
    
    for tier_dir in reversed(tier_paths):  # workspace → user → global
        memory_file = tier_dir / "MEMORY.md"
        if not memory_file.exists():
            continue
        
        try:
            with memory_file.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.rstrip('\n')
                    if keyword.lower() in line.lower() and line not in seen:
                        matched_lines.append(line)
                        seen.add(line)
        except Exception:
            continue
    
    if not matched_lines:
        return f"未找到包含 '{keyword}' 的记忆"
    
    return "\n".join(matched_lines)


# ── forget ──

_forget_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "forget",
        "description": "删除长期记忆中包含关键词的条目。删除不可逆。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "要删除的记忆关键词"},
                "level": {"type": "string", "enum": ["global", "user", "workspace"], "description": "层级，默认 user"},
            },
            "required": ["keyword"],
        },
    },
}


def forget_with_store(store, args: ToolArgs) -> str:
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


def _remember_exec(args: ToolArgs) -> str:
    return "Error: 记忆系统未初始化"


def _recall_exec(args: ToolArgs) -> str:
    return "Error: 记忆系统未初始化"


def _forget_exec(args: ToolArgs) -> str:
    return "Error: 记忆系统未初始化"


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition: ToolDefinition, execute):
        self.definition = definition
        self.execute = execute


remember_mod = _ToolMod(_remember_def, _remember_exec)
recall_mod = _ToolMod(_recall_def, _recall_exec)
forget_mod = _ToolMod(_forget_def, _forget_exec)

ALL_MEMORY_TOOLS = [remember_mod, recall_mod, forget_mod]
