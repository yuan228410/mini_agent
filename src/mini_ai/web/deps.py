"""Web 模式共享依赖初始化"""
import threading

from ..config import DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY, SKILL_PATHS, MCP, user_data_dir
from ..context import ContextBuilder
from ..memory import MemoryStore, Compactor, SessionManager
from ..memory.history_db import HistoryDB
from ..skills import SkillLoader
from ..subagents import SubagentLoader
from ..team import Blackboard, MessageBus, TeammateManager
from ..tools import register, register_subagents, register_team, register_blackboard
from ..workspace import WorkspaceManager

SKILL_LOADER = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
SUBAGENT_LOADER = SubagentLoader(PACKAGE_DIR / "subagents")

_MCP_LOADER = None


def _init_mcp():
    global _MCP_LOADER
    if not MCP.get("enabled") or not MCP.get("servers"):
        return
    try:
        from ..tools.mcp_loader import MCPLoader
    except ImportError:
        from ..logger import logger
        logger.warning("[MCP] mcp 包未安装，跳过 MCP 初始化 (pip install mcp)")
        return
    _MCP_LOADER = MCPLoader()
    modules = _MCP_LOADER.start_sync()
    if modules:
        from ..tools import _registry
        _registry.add_tools(*modules)
        from ..logger import logger
        logger.info(f"[MCP] 已注册 {len(modules)} 个 MCP 工具")

def shutdown_mcp():
    global _MCP_LOADER
    if _MCP_LOADER:
        _MCP_LOADER.stop_sync()
        _MCP_LOADER = None

def init_components():
    ws_mgr = WorkspaceManager(user_data_dir("default"))
    ws = ws_mgr.get("default") or ws_mgr.get("default")
    ws_dir = ws.ws_dir

    register(SKILL_LOADER)
    register_subagents(SUBAGENT_LOADER)

    bus = MessageBus(ws_dir / ".team" / "inbox")
    team_mgr = TeammateManager(
        team_dir=ws_dir / ".team",
        bus=bus,
        project_dir=ws_dir,
    )
    register_team(bus, team_mgr)

    bb = Blackboard(persist_path=ws_dir / ".team" / "blackboard.json")
    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
    register_blackboard(bb, workflow_dirs=workflow_dirs)

    return {
        "bus": bus,
        "team_mgr": team_mgr,
        "workspace_mgr": ws_mgr,
    }
