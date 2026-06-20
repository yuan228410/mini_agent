"""黑板工具 — blackboard_write / blackboard_read / blackboard_list"""
from ..core.runtime_types import BlackboardProtocol, ToolArgs, ToolDefinition

_blackboard: BlackboardProtocol | None = None


def configure(blackboard: BlackboardProtocol | None = None) -> None:
    global _blackboard
    if blackboard is not None:
        _blackboard = blackboard


def _require_blackboard() -> BlackboardProtocol:
    if _blackboard is None:
        raise RuntimeError("blackboard tools are not configured")
    return _blackboard


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


def _write_exec(args: ToolArgs) -> str:
    from ..tools.team_tools import _sender
    author = _sender()
    return _require_blackboard().put(_arg_text(args, "key"), _arg_text(args, "value"), author=author)


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


def _read_exec(args: ToolArgs) -> str:
    key = _arg_text(args, "key")
    missing = object()
    value = _require_blackboard().get(key, default=missing)
    if value is missing:
        return f"blackboard[{key}] 不存在"
    return value if isinstance(value, str) and value else "(空)"


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


def _list_exec(args: ToolArgs) -> str:
    keys = _require_blackboard().list_keys(_arg_text(args, "prefix"))
    if not keys:
        return "黑板为空"
    return "\n".join(keys)


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition: ToolDefinition, execute):
        self.definition = definition
        self.execute = execute


blackboard_write_mod = _ToolMod(_write_def, _write_exec)
blackboard_read_mod = _ToolMod(_read_def, _read_exec)
blackboard_list_mod = _ToolMod(_list_def, _list_exec)

ALL_BLACKBOARD_TOOLS = [blackboard_write_mod, blackboard_read_mod, blackboard_list_mod]
