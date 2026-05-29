"""技能接口"""
from fastapi import APIRouter, Query

from ...config import DATA_DIR, SKILL_PATHS, user_data_dir
from ...skills import SkillLoader

router = APIRouter()


def _get_skill_loader(username: str, workspace: str) -> SkillLoader:
    user_skills_dir = user_data_dir(username) / "skills"
    ws_dir = None
    if workspace:
        from ...workspace import WorkspaceManager
        ws_mgr = WorkspaceManager(user_data_dir(username), ensure_default=False)
        ws = ws_mgr.get(workspace)
        if ws:
            ws_dir = ws.ws_dir
    ws_skills_dir = ws_dir / "skills" if ws_dir else None
    return SkillLoader(DATA_DIR / "skills", SKILL_PATHS, user_skills_dir=user_skills_dir, workspace_skills_dir=ws_skills_dir)


@router.get("/skills")
async def list_skills(username: str = Query(default=""), workspace: str = Query(default="")):
    loader = _get_skill_loader(username, workspace)
    skills = []
    for name, skill in loader.skills.items():
        skills.append({
            "name": name,
            "description": skill["meta"].get("description", ""),
            "tags": skill["meta"].get("tags", ""),
            "tier": skill.get("tier", ""),
        })
    return {"skills": skills}


@router.delete("/skills/{name}")
async def delete_skill(name: str, username: str = Query(default=""), workspace: str = Query(default=""),
                        level: str = Query(default="")):
    loader = _get_skill_loader(username, workspace)
    if level:
        result = loader.delete_skill_at(name, level)
    else:
        result = loader.delete_skill(name)
    if result.startswith("Error:"):
        return {"ok": False, "error": result}
    return {"ok": True, "message": result}
