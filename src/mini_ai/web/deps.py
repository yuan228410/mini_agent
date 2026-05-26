"""Web 模式共享依赖初始化"""
import threading

from ..config import DATA_DIR, PACKAGE_DIR, COMPACTOR, MODEL_CONFIG, STREAMING, DISPLAY, SKILL_PATHS
from ..context import ContextBuilder
from ..memory import MemoryStore, Compactor, SessionManager
from ..skills import SkillLoader
from ..subagents import SubagentLoader
from ..team import Blackboard, MessageBus, TeammateManager
from ..tools import register, register_subagents, register_team, register_blackboard

SKILL_LOADER = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
SUBAGENT_LOADER = SubagentLoader(PACKAGE_DIR / "subagents")

def init_components():
    register(SKILL_LOADER)
    register_subagents(SUBAGENT_LOADER)

    bus = MessageBus(DATA_DIR / ".team" / "inbox")
    team_mgr = TeammateManager(
        team_dir=DATA_DIR / ".team",
        bus=bus,
        project_dir=DATA_DIR,
    )
    register_team(bus, team_mgr)

    bb = Blackboard(persist_path=DATA_DIR / ".team" / "blackboard.json")
    workflow_dirs = [DATA_DIR / "workflows", PACKAGE_DIR / "workflows"]
    register_blackboard(bb, workflow_dirs=workflow_dirs)

    store = MemoryStore(DATA_DIR / "memory_data")
    sessions = SessionManager(DATA_DIR / "memory_data" / "sessions")
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
    }
