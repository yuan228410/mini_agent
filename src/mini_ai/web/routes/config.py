"""状态配置接口"""
from fastapi import APIRouter, Query

from ...config import MODEL_CONFIG
from ...llm import _get_usage
from .chat import _get_or_create_session, _DEFAULT_SESSION

router = APIRouter()

@router.get("/config")
async def get_config(session_id: str = Query(default=_DEFAULT_SESSION), username: str = Query(default="default")):
    _, messages = _get_or_create_session(username, session_id)
    usage = _get_usage()
    return {
        "model": MODEL_CONFIG.get("model", "?"),
        "context_length": MODEL_CONFIG.get("context_length", 128000),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "system_prompt_chars": len(messages[0]["content"]) if messages else 0,
        "history_count": len(messages) - 1 if messages else 0,
        "session_id": session_id,
        "username": username,
    }
