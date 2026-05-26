"""Web 模式共享依赖初始化"""
import threading

from ..config import DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY, SKILL_PATHS, user_data_dir
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
