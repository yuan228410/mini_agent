"""状态配置接口"""
from fastapi import APIRouter, Query

from ... import __version__
from ...config import MODEL_CONFIG, get_model_config
from .chat import _get_or_create_session, _DEFAULT_SESSION, _SESSION_MODELS, _LAST_USAGE, _SESSION_PLAN_MODE

router = APIRouter()

@router.get("/config")
async def get_config(session_id: str = Query(default=_DEFAULT_SESSION), username: str = Query(default="default")):
    _, messages = _get_or_create_session(username, session_id, workspace=None)
    usage = _LAST_USAGE.get(f"{username}:{session_id}", {"prompt_tokens": 0, "completion_tokens": 0})
    model_name = _SESSION_MODELS.get(f"{username}:{session_id}")
    model_cfg = get_model_config(model_name) if model_name else MODEL_CONFIG
    return {
        "version": __version__,
        "model": model_cfg.get("model", "?"),
        "context_length": model_cfg.get("context_length", 128000),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "system_prompt_chars": len(messages[0]["content"]) if messages else 0,
        "history_count": len(messages) - 1 if messages else 0,
        "session_id": session_id,
        "username": username,
    }
