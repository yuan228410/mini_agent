#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Team 协作工具集：spawn_teammate, list_teammates, send_message, read_inbox, broadcast"""
import contextvars
import json
from ..core.runtime_types import ACTIVE_TEAM_MEMBER_STATUSES, InboxMessageTypeValue, MessageBusProtocol, TeamManagerProtocol, ToolArgs, ToolDefinition
from ..team.models import normalize_inbox_message_type
from ..logger import logger

_bus: MessageBusProtocol | None = None
_manager: TeamManagerProtocol | None = None

_caller = contextvars.ContextVar("team_caller", default="assistant")


def configure(bus: MessageBusProtocol | None = None, manager: TeamManagerProtocol | None = None) -> None:
    global _bus, _manager
    if bus is not None:
        _bus = bus
    if manager is not None:
        _manager = manager


def _sender() -> str: return _caller.get()


def _require_bus() -> MessageBusProtocol:
    if _bus is None:
        raise RuntimeError("team message bus is not configured")
    return _bus


def _require_manager() -> TeamManagerProtocol:
    if _manager is None:
        raise RuntimeError("team manager is not configured")
    return _manager


def _arg_text(args: ToolArgs, key: str, default: str = "") -> str:
    value = args.get(key, default)
    return value if isinstance(value, str) else str(value)


def _arg_msg_type(args: ToolArgs, key: str = "msg_type") -> InboxMessageTypeValue:
    return normalize_inbox_message_type(args.get(key, "message"))


def send_from_args(bus: MessageBusProtocol, sender: str, args: ToolArgs) -> str:
    return bus.send(sender, _arg_text(args, "to"), _arg_text(args, "content"), _arg_msg_type(args))


def broadcast_from_args(bus: MessageBusProtocol, manager: TeamManagerProtocol, sender: str, args: ToolArgs) -> str:
    return bus.broadcast(sender, _arg_text(args, "content"), manager.member_names())


def dismiss_team(bus: MessageBusProtocol, manager: TeamManagerProtocol) -> str:
    targets = []
    with manager.lock:
        for member in manager.config.get("members", []):
            if member["status"] in ACTIVE_TEAM_MEMBER_STATUSES:
                targets.append(member["name"])
    if not targets:
        return "当前没有活跃的队友"
    for name in targets:
        bus.send("lead", name, "任务结束，请退出。", "shutdown_request")
    return f"已发送 shutdown 请求给 {len(targets)} 位队友: {', '.join(targets)}"


# ── spawn_teammate ──

_spawn_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "spawn_teammate",
        "description": "召入持久队友。队友有独立线程，通过 inbox 收发消息。建议不超过 6 个队友。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "队友名字"},
                "role": {"type": "string", "description": "队友职司"},
                "prompt": {"type": "string", "description": "第一件任务"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
}


def _spawn(args: ToolArgs) -> str:
    return _require_manager().spawn(_arg_text(args, "name"), _arg_text(args, "role"), _arg_text(args, "prompt"))


# ── list_teammates ──

_list_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "list_teammates",
        "description": "列出 agent team 中所有队友的名字、职司和当前状态（idle/working/offline/shutdown）。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _list(args: ToolArgs) -> str:
    return _require_manager().list_all()


# ── send_message ──

_send_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "send_message",
        "description": "给队友发送消息。消息追加到对方 inbox，对方下次 read_inbox 时取出。",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人名字"},
                "content": {"type": "string", "description": "消息内容"},
                "msg_type": {"type": "string", "enum": ["message", "shutdown_request"], "description": "消息类型，默认 message"},
            },
            "required": ["to", "content"],
        },
    },
}


def _send(args: ToolArgs) -> str:
    caller = _sender()
    to = _arg_text(args, "to")
    logger.debug(f"[send→] caller={caller} to={to}")
    return send_from_args(_require_bus(), caller, args)


# ── read_inbox ──

_read_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "read_inbox",
        "description": "读取并清空自己的 inbox，获取所有待处理消息。读取后消息即删除。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read(args: ToolArgs) -> str:
    caller = _sender()
    logger.debug(f"[read_inbox] caller={caller}")
    messages = _require_bus().read_inbox(caller)
    return json.dumps(messages, ensure_ascii=False, indent=2)


# ── broadcast ──

_broadcast_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "broadcast",
        "description": "向所有队友广播消息。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "广播内容"},
            },
            "required": ["content"],
        },
    },
}


def _broadcast(args: ToolArgs) -> str:
    return broadcast_from_args(_require_bus(), _require_manager(), _sender(), args)


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition: ToolDefinition, execute):
        self.definition = definition
        self.execute = execute


_spawn_mod = _ToolMod(_spawn_def, _spawn)
_list_mod = _ToolMod(_list_def, _list)
_send_mod = _ToolMod(_send_def, _send)
_read_mod = _ToolMod(_read_def, _read)
_broadcast_mod = _ToolMod(_broadcast_def, _broadcast)

# ── dismiss_team ──

_dismiss_def: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "dismiss_team",
        "description": "解散所有队友（shutdown 全部 idle/working 状态的队友）。用于任务全部完成后释放资源。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _dismiss(args: ToolArgs) -> str:
    return dismiss_team(_require_bus(), _require_manager())


_dismiss_mod = _ToolMod(_dismiss_def, _dismiss)

ALL_TEAM_TOOLS = [_spawn_mod, _list_mod, _send_mod, _read_mod, _broadcast_mod, _dismiss_mod]


def set_caller(name: str):
    """设置当前线程的工具调用者身份（队友名或 lead）"""
    _caller.set(name)
