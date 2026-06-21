"""Runtime path and loader use cases shared by UI adapters."""
from __future__ import annotations

from pathlib import Path

from ..core.settings import SettingsSnapshot
from ..skills import SkillLoader
from ..subagents import SubagentLoader
from ..workspace import WorkspaceManager


def data_dir_for(settings: SettingsSnapshot) -> Path:
    """Return the configured data directory with a stable default."""

    return settings.paths.data_dir or Path.home() / ".mini_ai"


def package_dir_for(settings: SettingsSnapshot) -> Path:
    """Return the configured package directory with a local package fallback."""

    return settings.paths.package_dir or Path(__file__).resolve().parents[1]


def user_data_dir_for(settings: SettingsSnapshot, username: str) -> Path:
    """Return and create a user's data directory from runtime path settings."""

    user = username or "default"
    user_dir = data_dir_for(settings) / "users" / user
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def subagent_loader_for(settings: SettingsSnapshot) -> SubagentLoader:
    """Build a subagent loader from runtime package settings."""

    return SubagentLoader(package_dir_for(settings) / "subagents")


def context_builder_root_for(settings: SettingsSnapshot) -> Path:
    """Return the configured data root for context construction."""

    return data_dir_for(settings)


def memory_roots_for(settings: SettingsSnapshot, username: str, ws_dir: Path) -> tuple[Path, Path, Path]:
    """Return global, user and workspace memory roots from runtime settings."""

    data_dir = data_dir_for(settings)
    user_dir = user_data_dir_for(settings, username)
    return data_dir / "memory", user_dir / "memory", Path(ws_dir) / "memory_data"


def skill_loader_for(
    settings: SettingsSnapshot,
    *,
    user_skills_dir: Path | None = None,
    workspace_skills_dir: Path | None = None,
) -> SkillLoader:
    """Build a skill loader from runtime settings and optional writable tiers."""

    data_dir = data_dir_for(settings)
    return SkillLoader(
        data_dir / "skills",
        settings.paths.skill_paths,
        user_skills_dir=user_skills_dir,
        workspace_skills_dir=workspace_skills_dir,
    )


def skill_loader_for_user_workspace(settings: SettingsSnapshot, username: str = "", workspace: str = "") -> SkillLoader:
    """Build a skill loader for global, user and optional workspace tiers."""

    user_dir = user_data_dir_for(settings, username)
    ws_skills_dir = None
    if workspace:
        ws_mgr = WorkspaceManager(user_dir, ensure_default=False)
        ws = ws_mgr.get(workspace)
        if ws:
            ws_skills_dir = ws.ws_dir / "skills"
    return skill_loader_for(
        settings,
        user_skills_dir=user_dir / "skills",
        workspace_skills_dir=ws_skills_dir,
    )
