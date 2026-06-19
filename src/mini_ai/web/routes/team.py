"""Team 协作 API — 队友状态、黑板、解散"""
from fastapi import APIRouter, Query

from ...logger import logger
from ...team.models import TeamMemberSummary, TeamStatusResponse

router = APIRouter()


def _get_team_comp(username: str, workspace: str):
    from ..session_manager import SessionManager, ws_key
    wk = ws_key(username, workspace)
    return SessionManager.instance().get_team_component(wk)


@router.get("/team/status")
async def team_status(username: str = Query(...), workspace: str = Query("")) -> TeamStatusResponse:
    comp = _get_team_comp(username, workspace or None)
    if not comp:
        return {"teammates": [], "has_team": False}
    team_mgr = comp.get("team_mgr")
    if not team_mgr:
        return {"teammates": [], "has_team": False}
    members: list[TeamMemberSummary] = []
    for m in team_mgr.config.get("members", []):
        members.append({
            "name": m.get("name", ""),
            "role": m.get("role", ""),
            "status": m.get("status", "offline"),
        })
    return {"teammates": members, "has_team": True}


@router.get("/team/blackboard")
async def blackboard_snapshot(username: str = Query(...), workspace: str = Query("")):
    comp = _get_team_comp(username, workspace or None)
    if not comp:
        return {"entries": {}, "has_blackboard": False}
    bb = comp.get("blackboard")
    if not bb:
        return {"entries": {}, "has_blackboard": False}
    entries = bb.snapshot(detailed=True)
    return {"entries": entries, "has_blackboard": True}


@router.post("/team/dismiss")
async def dismiss_teammate(body: dict):
    username = body.get("username", "")
    workspace = body.get("workspace", "")
    name = body.get("name", "")
    if not username or not name:
        return {"error": "参数不完整"}
    comp = _get_team_comp(username, workspace or None)
    if not comp:
        return {"error": "Team 未初始化"}
    bus = comp.get("bus")
    if not bus:
        return {"error": "MessageBus 不可用"}
    bus.send("lead", name, "任务结束，请退出。", "shutdown_request")
    return {"status": "ok", "message": f"已发送 shutdown 请求给 {name}"}


@router.post("/team/blackboard/clear")
async def clear_blackboard(body: dict):
    username = body.get("username", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "参数不完整"}
    comp = _get_team_comp(username, workspace or None)
    if not comp:
        return {"error": "Team 未初始化"}
    bb = comp.get("blackboard")
    if not bb:
        return {"error": "黑板不可用"}
    bb.clear()
    return {"status": "ok", "message": "黑板已清空"}
