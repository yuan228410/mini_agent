"""Web 模式共享依赖初始化"""
import threading

from ..config import DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY, SKILL_PATHS, user_data_dir
from ..context import ContextBuilder
from ..memory import MemoryStore, Compactor, SessionManager
from ..memory.history_db import HistoryDB
from ..skills import SkillLoader
from ..subagents import SubagentLoader
from ..team import Blackboard, MessageBus, TeammateManager
from ..tools import register, register_subagents, register_team, register_blackboard, register_memory_tools, register_history_tools
from ..workspace import WorkspaceManager

SKILL_LOADER = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
SUBAGENT_LOADER = SubagentLoader(PACKAGE_DIR / "subagents")

def init_components():
    ws_mgr = WorkspaceManager(user_data_dir("default"))
    active_ws_name = "default"
    ws = ws_mgr.get(active_ws_name) or ws_mgr.get("default")
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

    store = MemoryStore(ws_dir / "memory_data")
    history_db = HistoryDB(ws_dir / "memory_data" / "history.db", workspace=active_ws_name)
    sessions = SessionManager(ws_dir / "memory_data" / "sessions")
    register_memory_tools(store)
    register_history_tools(history_db)
    ctx = ContextBuilder(DATA_DIR)

    compactor = Compactor(
        store,
        keep_recent=COMPACTOR["keep_recent"],
        char_threshold=COMPACTOR["char_threshold"],
        context_usage_threshold=COMPACTOR["context_usage_threshold"],
        context_length=MODEL_CONFIG.get("context_length", 128000),
        context_builder=ctx,
        skill_loader=SKILL_LOADER,
    )

    return {
        "store": store,
        "sessions": sessions,
        "ctx": ctx,
        "compactor": compactor,
        "bus": bus,
        "team_mgr": team_mgr,
        "workspace_mgr": ws_mgr,
    }
