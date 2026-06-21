"""Team 协作 API — 队友状态、黑板、解散"""
from fastapi import APIRouter, Query

from ...application import team_service
from ...core.runtime_types import TeamComponents
from ...team.models import TeamStatusResponse
from ..route_types import (
    BlackboardSnapshotResponse,
    ClearBlackboardRequest,
    DismissTeammateRequest,
    RouteErrorResponse,
    TeamActionResponse,
)
from ..runtime_helpers import team_component_for_user_workspace

router = APIRouter()


def _get_team_comp(username: str, workspace: str | None) -> TeamComponents | None:
    return team_component_for_user_workspace(username, workspace)


@router.get("/team/status")
async def team_status(username: str = Query(...), workspace: str = Query("")) -> TeamStatusResponse:
    return team_service.team_status(_get_team_comp(username, workspace or None))


@router.get("/team/blackboard")
async def blackboard_snapshot(username: str = Query(...), workspace: str = Query("")) -> BlackboardSnapshotResponse:
    return team_service.blackboard_snapshot(_get_team_comp(username, workspace or None))


@router.post("/team/dismiss")
async def dismiss_teammate(body: DismissTeammateRequest) -> TeamActionResponse | RouteErrorResponse:
    username = body.get("username", "")
    workspace = body.get("workspace", "")
    name = body.get("name", "")
    return team_service.dismiss_teammate(_get_team_comp(username, workspace or None), username, name)


@router.post("/team/blackboard/clear")
async def clear_blackboard(body: ClearBlackboardRequest) -> TeamActionResponse | RouteErrorResponse:
    username = body.get("username", "")
    workspace = body.get("workspace", "")
    return team_service.clear_blackboard(_get_team_comp(username, workspace or None), username)
