"""模型管理接口"""
from fastapi import APIRouter

from ..route_types import ModelsResponse, RouteErrorResponse, SwitchModelRequest, SwitchModelResponse
from ..runtime_helpers import current_settings_snapshot, model_config_for_name

router = APIRouter()

@router.get("/models")
async def list_models(session_id: str = "", workspace: str = "", username: str = "") -> ModelsResponse:
    settings = current_settings_snapshot()
    result = {
        "active": settings.model.model or "?",
        "active_name": settings.active_model_name,
        "models": [{"name": n, "model": cfg.get("model", "?")} for n, cfg in settings.model_configs.items()],
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
            session_cfg = settings.model_config_for(session_model)
            result["active"] = session_cfg.get("model", "?") if session_cfg else "?"
    return result

@router.post("/models/switch")
async def switch_model_endpoint(body: SwitchModelRequest) -> SwitchModelResponse | RouteErrorResponse:
    name = body.get("name", "").strip()
    username = body.get("username", "")
    session_id = body.get("session_id", "")
    workspace = body.get("workspace", "")
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    available = current_settings_snapshot().model_configs
    if name not in available:
        return {"error": f"未知模型: {name}，可选: {', '.join(available.keys())}"}
    cfg = model_config_for_name(name)
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
