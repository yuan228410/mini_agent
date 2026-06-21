"""Web 模式共享依赖初始化（仅全局级组件）"""
from ..core.runtime_factory import build_settings_snapshot
from ..skills import SkillLoader
from ..subagents import SubagentLoader

_SETTINGS = build_settings_snapshot()
SKILL_LOADER = SkillLoader(_SETTINGS.paths.data_dir / "skills", _SETTINGS.paths.skill_paths)
SUBAGENT_LOADER = SubagentLoader(_SETTINGS.paths.package_dir / "subagents")

_MCP_LOADER = None
MCP_SETTINGS = _SETTINGS.mcp


def _init_mcp():
    global _MCP_LOADER, MCP_SETTINGS
    settings = build_settings_snapshot().mcp
    MCP_SETTINGS = settings
    if not settings.enabled or not settings.servers:
        return
    try:
        from ..tools.mcp_loader import MCPLoader
    except ImportError:
        from ..logger import logger
        logger.warning("[MCP] mcp 包未安装，跳过 MCP 初始化 (pip install mcp)")
        return
    _MCP_LOADER = MCPLoader(settings)
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
