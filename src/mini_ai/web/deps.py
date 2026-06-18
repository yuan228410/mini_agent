"""Web 模式共享依赖初始化（仅全局级组件）"""
from ..config import DATA_DIR, PACKAGE_DIR, SKILL_PATHS, MCP
from ..skills import SkillLoader
from ..subagents import SubagentLoader

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
        from ..logger import logger
        logger.info(f"[MCP] 已加载 {len(modules)} 个 MCP 工具")

def shutdown_mcp():
    global _MCP_LOADER
    if _MCP_LOADER:
        _MCP_LOADER.stop_sync()
        _MCP_LOADER = None

def init_components():
    """初始化全局组件（与用户无关的），不注册到运行时全局工具表。"""
    _init_mcp()
