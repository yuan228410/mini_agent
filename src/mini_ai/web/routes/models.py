"""模型管理接口"""
from fastapi import APIRouter

from ...application import model_use_cases
from ..route_types import ModelsResponse, RouteErrorResponse, SwitchModelRequest, SwitchModelResponse
from ..config_dependencies import model_route_dependencies
from ..runtime_helpers import current_settings_snapshot

router = APIRouter()


@router.get("/models")
async def list_models(session_id: str = "", workspace: str = "", username: str = "") -> ModelsResponse:
    return model_use_cases.list_models(
        current_settings_snapshot(),
        deps=model_route_dependencies(),
        session_id=session_id,
        workspace=workspace,
        username=username,
    )


@router.post("/models/switch")
async def switch_model_endpoint(body: SwitchModelRequest) -> SwitchModelResponse | RouteErrorResponse:
    return model_use_cases.switch_session_model(
        current_settings_snapshot(),
        model_route_dependencies(),
        name=body.get("name", "").strip(),
        username=body.get("username", ""),
        session_id=body.get("session_id", "") or "default",
        workspace=body.get("workspace", ""),
    )
