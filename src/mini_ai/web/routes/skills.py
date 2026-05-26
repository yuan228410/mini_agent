"""技能接口"""
from fastapi import APIRouter

from ...tools import dispatch

router = APIRouter()

@router.get("/skills")
async def list_skills():
    result = dispatch("list_skills", {})
    return {"skills": result}
