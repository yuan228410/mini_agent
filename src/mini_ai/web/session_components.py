"""Web session component factory."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..config import user_data_dir
from ..core.runtime_factory import build_settings_snapshot
from ..core.runtime_types import SessionComponents, TeamComponents
from ..logger import logger
from ..workspace import WorkspaceManager


ResolveBase = Callable[[str, str | None], Path]
TeamGetter = Callable[[str], TeamComponents | None]
TeamSetter = Callable[[str, TeamComponents], None]
TeamExists = Callable[[str], bool]
WorkspaceKey = Callable[[str, str | None], str]


def create_session_components(
    username: str,
    sid: str,
    base: Path | None,
    workspace: str | None,
    *,
    resolve_base: ResolveBase,
    ws_key: WorkspaceKey,
    get_team_component: TeamGetter,
    set_team_component: TeamSetter,
    has_team_component: TeamExists,
) -> SessionComponents:
    """Create all runtime components for a Web chat session."""

    from ..context import ContextBuilder
    from ..memory import Compactor, HistoryDBPool, MemoryStore
    from ..skills import SkillLoader

    project_path = ""
    ws_dir = None
    if workspace:
        ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
        ws = ws_mgr.get(workspace)
        if ws:
            project_path = ws.project_path
            ws_dir = ws.ws_dir
        else:
            logger.warning(f"[Web] 工作空间 '{workspace}' 不存在，使用默认配置")

    settings = build_settings_snapshot()

    user_dir = user_data_dir(username)
    user_memory_dir = user_dir / "memory"

    if base is None:
        base = resolve_base(username, workspace or "default")
    session_dir = base / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    session_memory_dir = session_dir / "memory_data"
    session_memory_dir.mkdir(parents=True, exist_ok=True)

    data_dir = settings.paths.data_dir or (user_dir.parent.parent if user_dir.parent.name == "users" else user_dir.parent)
    global_memory_dir = data_dir / "memory"
    ws_memory_dir = ws_dir / "memory_data" if ws_dir else None
    user_store = MemoryStore(
        user_memory_dir,
        episode_dir=session_memory_dir,
        global_memory_dir=global_memory_dir,
        workspace_memory_dir=ws_memory_dir,
    )

    history_db = HistoryDBPool.get(
        username,
        data_dir=data_dir,
        history_settings=settings.database.history,
    )

    user_skills_dir = user_data_dir(username) / "skills"
    ws_skills_dir = ws_dir / "skills" if ws_dir else None
    skill_loader = SkillLoader(
        data_dir / "skills",
        settings.paths.skill_paths,
        user_skills_dir=user_skills_dir,
        workspace_skills_dir=ws_skills_dir,
    )

    ctx_builder = ContextBuilder(data_dir)

    compactor_settings = settings.compactor
    compactor = Compactor(
        user_store,
        keep_recent=compactor_settings.keep_recent,
        context_usage_threshold=compactor_settings.context_usage_threshold,
        keep_budget_ratio=compactor_settings.keep_budget_ratio,
        early_compact_ratio=compactor_settings.early_compact_ratio,
        max_cached_summaries=compactor_settings.max_cached_summaries,
        max_summary_sections=compactor_settings.max_summary_sections,
        context_length=settings.model.context_length,
        context_builder=ctx_builder,
        skill_loader=skill_loader,
        project_path=project_path,
        summary_dir=session_dir,
    )

    components: SessionComponents = {
        "store": user_store,
        "history_db": history_db,
        "compactor": compactor,
        "ctx_builder": ctx_builder,
        "project_path": project_path,
        "skill_loader": skill_loader,
        "settings": settings,
    }

    wk = ws_key(username, workspace)
    if ws_dir and not has_team_component(wk):
        from ..team import Blackboard, MessageBus, TeammateManager

        team_dir = ws_dir / ".team"
        bus = MessageBus(team_dir / "inbox")
        team_mgr = TeammateManager(
            team_dir=team_dir,
            bus=bus,
            project_dir=ws_dir,
            team_settings=settings.team,
            timeout_settings=settings.timeouts,
        )
        bb = Blackboard(persist_path=team_dir / "blackboard.json")
        set_team_component(wk, {"bus": bus, "team_mgr": team_mgr, "blackboard": bb})

    team_comp = get_team_component(wk) or {}
    components["bus"] = team_comp.get("bus")
    components["team_mgr"] = team_comp.get("team_mgr")
    components["blackboard"] = team_comp.get("blackboard")

    return components
