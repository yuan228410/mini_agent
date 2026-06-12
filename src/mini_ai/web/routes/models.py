"""模型管理接口"""
from fastapi import APIRouter

from ...config import AVAILABLE_MODELS, MODEL_CONFIG, get_model_config

router = APIRouter()

@router.get("/models")
async def list_models(session_id: str = "", workspace: str = "", username: str = ""):
    result = {
        "active": MODEL_CONFIG.get("model", "?"),
        "active_name": _get_active_name(),
        "models": [{"name": n, "model": _models_raw().get(n, {}).get("model", "?")} for n in AVAILABLE_MODELS],
    }
    # 如果传了 session_id，返回该会话的专属模型
    if session_id and username:
        from ..session_manager import SessionManager, cache_key, _load_session_model, resolve_base
        sm = SessionManager.instance()
        key = cache_key(username, workspace or None, session_id)
        session_model = sm.get_model(key)
        if not session_model:
            base = resolve_base(username, workspace or None)
            session_model = _load_session_model(base, session_id)
        if session_model:
            result["active_name"] = session_model
            result["active"] = _models_raw().get(session_model, {}).get("model", "?")
    return result

@router.post("/models/switch")
async def switch_model_endpoint(body: dict):
    name = body.get("name", "").strip()
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    if name not in AVAILABLE_MODELS:
        return {"error": f"未知模型: {name}，可选: {', '.join(AVAILABLE_MODELS)}"}
    cfg = get_model_config(name)
    if not cfg:
        return {"error": f"模型配置无效: {name}"}
    from ..session_manager import SessionManager, cache_key, _save_session_model, resolve_base
    sm = SessionManager.instance()
    key = cache_key(username, workspace or None, session_id)
    sm.set_model(key, name)
    # 持久化到 meta.json（使用与 _build_meta 一致的路径）
    base = resolve_base(username, workspace or None)
    _save_session_model(base, session_id, name)
    return {"status": "ok", "active_name": name, "model": cfg.get("model", "?")}

def _get_active_name():
    from ...config import _raw
    return _raw.get("active_model", "")

def _models_raw():
    from ...config import _raw
    return _raw.get("models") or {}
