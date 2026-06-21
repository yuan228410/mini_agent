"""Web command catalog and MCP status use cases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class McpStatusDependencies:
    settings: Any
    loader: Any | None = None


WEB_COMMANDS: list[dict[str, Any]] = [
    {"name": "/plan", "desc": "进入计划模式：讨论方案、生成选项、确认后执行", "has_arg": False},
    {"name": "/act", "desc": "批准当前计划并执行", "has_arg": False},
    {"name": "/clear", "desc": "清空当前会话消息（归档）", "has_arg": False},
    {"name": "/purge", "desc": "彻底删除历史消息（不可恢复）", "has_arg": False},
    {"name": "/compact", "desc": "手动触发对话压缩", "has_arg": False},
    {"name": "/genskill", "desc": "从对话生成技能", "has_arg": True, "arg_name": "技能名称"},
    {"name": "/skill install", "desc": "安装技能", "has_arg": True, "arg_name": "URL或路径 [--global/--user/--workspace]"},
    {"name": "/skill create", "desc": "创建技能模板", "has_arg": True, "arg_name": "技能名称 [--global/--user/--workspace]"},
    {"name": "/thinking", "desc": "设置思考展示模式", "has_arg": True, "arg_name": "collapsed/expanded/hidden"},
    {"name": "/prompt", "desc": "预览系统提示词（含 token 数）", "has_arg": False},
    {"name": "/tools", "desc": "预览工具定义（含 token 数）", "has_arg": False},
]


def list_commands() -> dict[str, Any]:
    """Return supported Web slash commands."""

    return {"commands": [dict(command) for command in WEB_COMMANDS]}


def mcp_status(deps: McpStatusDependencies) -> dict[str, Any]:
    """Return configured and connected MCP server status."""

    if not deps.settings.enabled:
        return {"enabled": False, "servers": []}
    servers = []
    configured_servers = deps.settings.servers
    if deps.loader:
        configured_servers = deps.loader.servers
        for name, conn in deps.loader._connections.items():
            servers.append({
                "name": name,
                "type": conn.conn_type,
                "tools": [{"name": t.name, "description": t.description} for t in conn.tools],
            })
    configured = [
        {"name": n, "type": c.get("type", "stdio"), "disabled": c.get("disabled", False)}
        for n, c in configured_servers.items()
    ]
    return {"enabled": True, "configured": configured, "connected": servers}
