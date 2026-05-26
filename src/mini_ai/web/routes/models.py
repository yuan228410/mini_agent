"""模型管理接口"""
from fastapi import APIRouter

from ...config import AVAILABLE_MODELS, MODEL_CONFIG, switch_model

router = APIRouter()

@router.get("/models")
async def list_models():
    return {
        "active": MODEL_CONFIG.get("model", "?"),
        "active_name": _get_active_name(),
        "models": [{"name": n, "model": _models_raw().get(n, {}).get("model", "?")} for n in AVAILABLE_MODELS],
    }

@router.post("/models/switch")
async def switch_model_endpoint(body: dict):
    name = body.get("name", "").strip()
    if name not in AVAILABLE_MODELS:
        return {"error": f"未知模型: {name}，可选: {', '.join(AVAILABLE_MODELS)}"}
    err = switch_model(name)
    if err:
        return {"error": err}
    return {"status": "ok", "active_name": name, "model": MODEL_CONFIG.get("model", "?")}

def _get_active_name():
    from ...config import _raw
    return _raw.get("active_model", "")

def _models_raw():
    from ...config import _raw
    return _raw.get("models") or {}
