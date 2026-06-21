"""Team collaboration use cases shared by UI adapters."""
from __future__ import annotations

from typing import Any, Protocol

from ..core.runtime_types import TeamComponents


class TeamComponentStorePort(Protocol):
    def get_team_component(self, key: str) -> TeamComponents | None: ...


def get_team_component(store: TeamComponentStorePort, ws_key, username: str, workspace: str | None) -> TeamComponents | None:
    """Return team components for a user/workspace pair."""

    return store.get_team_component(ws_key(username, workspace))


def team_status(comp: TeamComponents | None) -> dict[str, Any]:
    """Return current team status."""

    if not comp:
        return {"teammates": [], "has_team": False}
    team_mgr = comp.get("team_mgr")
    if not team_mgr:
        return {"teammates": [], "has_team": False}
    return {"teammates": team_mgr.member_summaries(), "has_team": True}


def blackboard_snapshot(comp: TeamComponents | None) -> dict[str, Any]:
    """Return detailed blackboard entries."""

    if not comp:
        return {"entries": {}, "has_blackboard": False}
    bb = comp.get("blackboard")
    if not bb:
        return {"entries": {}, "has_blackboard": False}
    return {"entries": bb.snapshot(detailed=True), "has_blackboard": True}


def dismiss_teammate(comp: TeamComponents | None, username: str, name: str) -> dict[str, Any]:
    """Send a shutdown request to a teammate."""

    if not username or not name:
        return {"error": "参数不完整"}
    if not comp:
        return {"error": "Team 未初始化"}
    bus = comp.get("bus")
    if not bus:
        return {"error": "MessageBus 不可用"}
    bus.send("lead", name, "任务结束，请退出。", "shutdown_request")
    return {"status": "ok", "message": f"已发送 shutdown 请求给 {name}"}


def clear_blackboard(comp: TeamComponents | None, username: str) -> dict[str, Any]:
    """Clear team blackboard entries."""

    if not username:
        return {"error": "参数不完整"}
    if not comp:
        return {"error": "Team 未初始化"}
    bb = comp.get("blackboard")
    if not bb:
        return {"error": "黑板不可用"}
    bb.clear()
    return {"status": "ok", "message": "黑板已清空"}
