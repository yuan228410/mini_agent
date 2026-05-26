"""黑板工具 — blackboard_write / blackboard_read / blackboard_list"""
from ..logger import logger

_blackboard = None


def configure(blackboard=None):
    global _blackboard
    if blackboard is not None:
        _blackboard = blackboard


# ── blackboard_write ──

_write_def = {
    "type": "function",
    "function": {
        "name": "blackboard_write",
        "description": "向共享黑板写入一条数据。其他 agent 可通过 blackboard_read 读取。适合传递搜索结果、分析结论、代码片段等跨 agent 共享信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "数据的唯一标识，例如 search_result、design_doc"},
                "value": {"type": "string", "description": "要存储的内容"},
            },
            "required": ["key", "value"],
        },
    },
}


def _write_exec(args: dict) -> str:
    from ..tools.team_tools import _sender
    author = _sender()
    return _blackboard.put(args["key"], args["value"], author=author)


# ── blackboard_read ──

_read_def = {
    "type": "function",
    "function": {
        "name": "blackboard_read",
        "description": "从共享黑板读取指定 key 的数据。用于获取其他 agent 写入的结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "要读取的 key"},
            },
            "required": ["key"],
        },
    },
}


def _read_exec(args: dict) -> str:
    value = _blackboard.get(args["key"])
    if not value:
        return f"blackboard[{args['key']}] 不存在"
    return value


# ── blackboard_list ──

_list_def = {
    "type": "function",
    "function": {
        "name": "blackboard_list",
        "description": "列出共享黑板上的所有 key（可按前缀过滤）。",
        "parameters": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "可选前缀过滤，默认列出全部"},
            },
        },
    },
}


def _list_exec(args: dict) -> str:
    keys = _blackboard.list_keys(args.get("prefix", ""))
    if not keys:
        return "黑板为空"
    return "\n".join(keys)


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition, execute):
        self.definition = definition
        self.execute = execute


blackboard_write_mod = _ToolMod(_write_def, _write_exec)
blackboard_read_mod = _ToolMod(_read_def, _read_exec)
blackboard_list_mod = _ToolMod(_list_def, _list_exec)

ALL_BLACKBOARD_TOOLS = [blackboard_write_mod, blackboard_read_mod, blackboard_list_mod]
