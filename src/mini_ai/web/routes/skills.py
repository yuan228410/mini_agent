"""技能接口"""
from fastapi import APIRouter, Query, HTTPException
from pathlib import Path

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
    """列出所有技能"""
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


@router.get("/skills/{name}")
async def get_skill_info(name: str, username: str = Query(default=""), workspace: str = Query(default="")):
    """获取技能详情"""
    loader = _get_skill_loader(username, workspace)
    if name not in loader.skills:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    
    skill = loader.skills[name]
    meta = skill["meta"]
    
    return {
        "name": name,
        "description": meta.get("description", ""),
        "tags": meta.get("tags", ""),
        "tier": skill.get("tier", "global"),
        "path": skill["path"],
        "content": skill["body"],
    }


@router.post("/skills/{name}/load")
async def load_skill(name: str, username: str = Query(default=""), workspace: str = Query(default="")):
    """加载技能"""
    loader = _get_skill_loader(username, workspace)
    if name not in loader.skills:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    
    skill = loader.skills[name]
    return {
        "ok": True,
        "name": name,
        "content": skill["body"],
        "meta": skill["meta"],
    }


@router.post("/skills/install")
async def install_skill(
    source: str = Query(..., description="技能 URL 或路径"),
    level: str = Query(default="global", description="安装层级: global/user/workspace"),
    username: str = Query(default=""),
    workspace: str = Query(default=""),
):
    """安装技能"""
    from ...tools import dispatch
    result = dispatch("install_skill", {"source": source, "level": level})
    
    if result.startswith("Error:") or "失败" in result:
        return {"ok": False, "error": result}
    
    return {"ok": True, "message": result}


@router.post("/skills/{name}/create")
async def create_skill(
    name: str,
    level: str = Query(default="global", description="创建层级: global/user/workspace"),
    username: str = Query(default=""),
    workspace: str = Query(default=""),
):
    """创建技能模板"""
    loader = _get_skill_loader(username, workspace)
    
    target_dir = loader.get_tier_dir(level)
    if not target_dir:
        raise HTTPException(status_code=400, detail=f"层级 '{level}' 未配置")
    
    skill_dir = target_dir / name
    if skill_dir.exists():
        raise HTTPException(status_code=400, detail=f"技能 '{name}' 已存在于 {level} 层级")
    
    # 创建目录和模板文件
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    
    template = f"""---
name: {name}
description: 技能描述（请修改）
tags: 标签1,标签2
---

# {name}

技能内容（请修改）

## 使用场景

- 场景1
- 场景2

## 步骤

1. 步骤1
2. 步骤2
"""
    skill_file.write_text(template, encoding="utf-8")
    
    return {
        "ok": True,
        "message": f"技能模板 '{name}' 已创建",
        "path": str(skill_dir),
    }


@router.delete("/skills/{name}")
async def delete_skill(
    name: str,
    username: str = Query(default=""),
    workspace: str = Query(default=""),
    level: str = Query(default=""),
):
    """卸载技能"""
    loader = _get_skill_loader(username, workspace)
    if level:
        result = loader.delete_skill_at(name, level)
    else:
        result = loader.delete_skill(name)
    if result.startswith("Error:"):
        return {"ok": False, "error": result}
    return {"ok": True, "message": result}
