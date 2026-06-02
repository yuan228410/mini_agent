"""斜杠命令列表接口"""
from fastapi import APIRouter

router = APIRouter()

_WEB_COMMANDS = [
    {"name": "/plan", "desc": "进入计划模式（只规划不执行）", "has_arg": False},
    {"name": "/act", "desc": "切换到执行模式", "has_arg": False},
    {"name": "/clear", "desc": "清空当前会话消息（归档）", "has_arg": False},
    {"name": "/purge", "desc": "彻底删除历史消息（不可恢复）", "has_arg": False},
    {"name": "/compact", "desc": "手动触发对话压缩", "has_arg": False},
    {"name": "/genskill", "desc": "从对话生成技能", "has_arg": True, "arg_name": "技能名称"},
    {"name": "/skill install", "desc": "安装技能", "has_arg": True, "arg_name": "URL或路径 [--global/--user/--workspace]"},
    {"name": "/skill create", "desc": "创建技能模板", "has_arg": True, "arg_name": "技能名称 [--global/--user/--workspace]"},
    {"name": "/thinking", "desc": "设置思考展示模式", "has_arg": True, "arg_name": "collapsed/expanded/hidden"},
    {"name": "/prompt", "desc": "预览系统提示词", "has_arg": False},
]


@router.get("/commands")
async def list_commands():
    return {"commands": _WEB_COMMANDS}


@router.get("/mcp")
async def mcp_status():
    from ...config import MCP
    if not MCP.get("enabled"):
        return {"enabled": False, "servers": []}
    from ...tools.mcp_loader import _MCP_SERVERS
    from ..deps import _MCP_LOADER
    servers = []
    if _MCP_LOADER:
        for name, conn in _MCP_LOADER._connections.items():
            servers.append({
                "name": name,
                "type": conn.conn_type,
                "tools": [{"name": t.name, "description": t.description} for t in conn.tools],
            })
    configured = [{"name": n, "type": c.get("type", "stdio"), "disabled": c.get("disabled", False)}
                  for n, c in _MCP_SERVERS.items()]
    return {"enabled": True, "configured": configured, "connected": servers}
