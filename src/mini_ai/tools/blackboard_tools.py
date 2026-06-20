"""黑板工具 — blackboard_write / blackboard_read / blackboard_list"""
from ..core.runtime_types import BlackboardProtocol, ToolArgs, ToolDefinition

def _arg_text(args: ToolArgs, key: str, default: str = "") -> str:
    value = args.get(key, default)
    return value if isinstance(value, str) else str(value)


# ── blackboard_write ──

_write_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "blackboard_write",
        "description": "向共享黑板写入数据。相同 key 会覆盖。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "数据标识，建议格式：角色_主题"},
                "value": {"type": "string", "description": "要存储的内容"},
            },
            "required": ["key", "value"],
        },
    },
}


def write_to_blackboard(blackboard: BlackboardProtocol, args: ToolArgs, author: str = "") -> str:
    return blackboard.put(_arg_text(args, "key"), _arg_text(args, "value"), author=author)


def _write_exec(args: ToolArgs) -> str:
    return "Error: 黑板未配置"


# ── blackboard_read ──

_read_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "blackboard_read",
        "description": "从共享黑板读取数据。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "要读取的 key"},
            },
            "required": ["key"],
        },
    },
}


def read_from_blackboard(blackboard: BlackboardProtocol, args: ToolArgs) -> str:
    key = _arg_text(args, "key")
    missing = object()
    value = blackboard.get(key, default=missing)
    if value is missing:
        return f"blackboard[{key}] 不存在"
    return value if isinstance(value, str) and value else "(空)"


def _read_exec(args: ToolArgs) -> str:
    return "Error: 黑板未配置"


# ── blackboard_list ──

_list_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "blackboard_list",
        "description": "列出黑板上的所有 key。",
        "parameters": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "前缀过滤"},
            },
        },
    },
}


def list_blackboard_keys(blackboard: BlackboardProtocol, args: ToolArgs) -> str:
    keys = blackboard.list_keys(_arg_text(args, "prefix"))
    if not keys:
        return "黑板为空"
    return "\n".join(keys)


def _list_exec(args: ToolArgs) -> str:
    return "Error: 黑板未配置"


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition: ToolDefinition, execute):
        self.definition = definition
        self.execute = execute


blackboard_write_mod = _ToolMod(_write_def, _write_exec)
blackboard_read_mod = _ToolMod(_read_def, _read_exec)
blackboard_list_mod = _ToolMod(_list_def, _list_exec)

ALL_BLACKBOARD_TOOLS = [blackboard_write_mod, blackboard_read_mod, blackboard_list_mod]
