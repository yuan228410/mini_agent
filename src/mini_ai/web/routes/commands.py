"""斜杠命令列表接口"""
from fastapi import APIRouter

from ..route_types import CommandsResponse, McpStatusResponse, WebCommand

router = APIRouter()

_WEB_COMMANDS: list[WebCommand] = [
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


@router.get("/commands")
async def list_commands() -> CommandsResponse:
    return {"commands": _WEB_COMMANDS}


@router.get("/mcp")
async def mcp_status() -> McpStatusResponse:
    from ..deps import MCP_SETTINGS, _MCP_LOADER
    if not MCP_SETTINGS.enabled:
        return {"enabled": False, "servers": []}
    servers = []
    configured_servers = MCP_SETTINGS.servers
    if _MCP_LOADER:
        configured_servers = _MCP_LOADER.servers
        for name, conn in _MCP_LOADER._connections.items():
            servers.append({
                "name": name,
                "type": conn.conn_type,
                "tools": [{"name": t.name, "description": t.description} for t in conn.tools],
            })
    configured = [{"name": n, "type": c.get("type", "stdio"), "disabled": c.get("disabled", False)}
                  for n, c in configured_servers.items()]
    return {"enabled": True, "configured": configured, "connected": servers}
