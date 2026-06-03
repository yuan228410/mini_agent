#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Team 协作工具集：spawn_teammate, list_teammates, send_message, read_inbox, broadcast"""
import contextvars
import json

from ..logger import logger

_bus = None
_manager = None

_caller = contextvars.ContextVar("team_caller", default="assistant")


def configure(bus=None, manager=None):
    global _bus, _manager
    if bus is not None:
        _bus = bus
    if manager is not None:
        _manager = manager

def _sender() -> str: return _caller.get()


# ── spawn_teammate ──

_spawn_def = {
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


def _spawn(args: dict) -> str:
    return _manager.spawn(args.get("name", ""), args.get("role", ""), args.get("prompt", ""))


# ── list_teammates ──

_list_def = {
    "type": "function",
    "function": {
        "name": "list_teammates",
        "description": "列出 agent team 中所有队友的名字、职司和当前状态（idle/working/offline/shutdown）。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _list(args: dict) -> str:
    return _manager.list_all()


# ── send_message ──

_send_def = {
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


def _send(args: dict) -> str:
    caller = _sender()
    to = args.get("to", "")
    logger.debug(f"[send→] caller={caller} to={to}")
    return _bus.send(caller, to, args.get("content", ""),
                     args.get("msg_type", "message"))


# ── read_inbox ──

_read_def = {
    "type": "function",
    "function": {
        "name": "read_inbox",
        "description": "读取并清空自己的 inbox，获取所有待处理消息。读取后消息即删除。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read(args: dict) -> str:
    caller = _sender()
    logger.debug(f"[read_inbox] caller={caller}")
    messages = _bus.read_inbox(caller)
    return json.dumps(messages, ensure_ascii=False, indent=2)


# ── broadcast ──

_broadcast_def = {
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


def _broadcast(args: dict) -> str:
    return _bus.broadcast(_sender(), args.get("content", ""), _manager.member_names())


# ── 构建可注册的工具模块对象 ──

class _ToolMod:
    def __init__(self, definition, execute):
        self.definition = definition
        self.execute = execute


_spawn_mod = _ToolMod(_spawn_def, _spawn)
_list_mod = _ToolMod(_list_def, _list)
_send_mod = _ToolMod(_send_def, _send)
_read_mod = _ToolMod(_read_def, _read)
_broadcast_mod = _ToolMod(_broadcast_def, _broadcast)

# ── dismiss_team ──

_dismiss_def = {
    "type": "function",
    "function": {
        "name": "dismiss_team",
        "description": "解散所有队友（shutdown 全部 idle/working 状态的队友）。用于任务全部完成后释放资源。",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _dismiss(args: dict) -> str:
    targets = []
    with _manager.lock:
        for m in _manager.config.get("members", []):
            if m["status"] in ("idle", "working"):
                targets.append(m["name"])
    if not targets:
        return "当前没有活跃的队友"
    for name in targets:
        _bus.send("lead", name, "任务结束，请退出。", "shutdown_request")
    return f"已发送 shutdown 请求给 {len(targets)} 位队友: {', '.join(targets)}"


_dismiss_mod = _ToolMod(_dismiss_def, _dismiss)

ALL_TEAM_TOOLS = [_spawn_mod, _list_mod, _send_mod, _read_mod, _broadcast_mod, _dismiss_mod]


def set_caller(name: str):
    """设置当前线程的工具调用者身份（队友名或 lead）"""
    _caller.set(name)
