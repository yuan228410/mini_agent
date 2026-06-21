"""技能接口"""
from fastapi import APIRouter, Query, HTTPException

from ...application import skill_service
from ...application.skill_service import SkillServiceError
from ..runtime_helpers import skill_loader_for_user_workspace
from ..route_types import (
    RouteErrorResponse,
    SkillCreateResponse,
    SkillDeleteResponse,
    SkillInfoResponse,
    SkillInstallResponse,
    SkillLoadResponse,
    SkillsListResponse,
)

router = APIRouter()


def _get_skill_loader(username: str, workspace: str):
    return skill_loader_for_user_workspace(username, workspace)


@router.get("/skills")
async def list_skills(username: str = Query(default=""), workspace: str = Query(default="")) -> SkillsListResponse:
    """列出所有技能"""
    return skill_service.list_skills(_get_skill_loader(username, workspace))


@router.get("/skills/{name}")
async def get_skill_info(name: str, username: str = Query(default=""), workspace: str = Query(default="")) -> SkillInfoResponse:
    """获取技能详情"""
    try:
        return skill_service.get_skill_info(_get_skill_loader(username, workspace), name)
    except SkillServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/skills/{name}/load")
async def load_skill(name: str, username: str = Query(default=""), workspace: str = Query(default="")) -> SkillLoadResponse:
    """加载技能"""
    try:
        return skill_service.load_skill(_get_skill_loader(username, workspace), name)
    except SkillServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/skills/install")
async def install_skill(
    source: str = Query(..., description="技能 URL 或路径"),
    level: str = Query(default="global", description="安装层级: global/user/workspace"),
    username: str = Query(default=""),
    workspace: str = Query(default=""),
) -> SkillInstallResponse | RouteErrorResponse:
    """安装技能"""
    return skill_service.install_skill(_get_skill_loader(username, workspace), source, level)


@router.post("/skills/{name}/create")
async def create_skill(
    name: str,
    level: str = Query(default="global", description="创建层级: global/user/workspace"),
    username: str = Query(default=""),
    workspace: str = Query(default=""),
) -> SkillCreateResponse:
    """创建技能模板"""
    try:
        return skill_service.create_skill_template(_get_skill_loader(username, workspace), name, level)
    except SkillServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/skills/{name}")
async def delete_skill(
    name: str,
    username: str = Query(default=""),
    workspace: str = Query(default=""),
    level: str = Query(default=""),
) -> SkillDeleteResponse | RouteErrorResponse:
    """卸载技能"""
    return skill_service.delete_skill(_get_skill_loader(username, workspace), name, level)
