"""状态配置接口"""
import time

from fastapi import APIRouter, Query

from ... import __version__
from ...application import config_service
from ...logger import logger
from ..route_types import (
    AddModelRequest,
    AddModelResponse,
    ConfigResponse,
    McpServerAddRequest,
    McpServerAddResponse,
    McpServerRemoveResponse,
    RemoveModelRequest,
    RemoveModelResponse,
    RouteErrorResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    SystemPromptResponse,
    ToolsResponse,
)
from ..runtime_helpers import config_mutation_dependencies, config_preview_dependencies, current_settings_snapshot

router = APIRouter()

@router.get("/config")
async def get_config(session_id: str = Query(default=""), username: str = Query(...), workspace: str = Query(default="")) -> ConfigResponse | RouteErrorResponse:
    _t0 = time.time()
    if not username:
        return {"error": "缺少 username"}
    if not session_id:
        session_id = "default"
    result = config_service.config_summary(
        current_settings_snapshot(),
        config_preview_dependencies(),
        version=__version__,
        username=username,
        session_id=session_id,
        workspace=workspace,
    )
    logger.debug(f"[perf] get_config sid={session_id} ws={workspace} time={time.time()-_t0:.3f}s")
    return result


@router.get("/config/system-prompt")
async def get_system_prompt(username: str = Query(default=""), workspace: str = Query(default="")) -> SystemPromptResponse | RouteErrorResponse:
    """获取完整系统提示词（含字符数和 token 估算）"""
    if not username:
        return {"error": "缺少 username"}
    
    return config_service.system_prompt_preview(config_preview_dependencies(), username=username, workspace=workspace)


@router.get("/config/tools")
async def get_tools(username: str = Query(default=""), workspace: str = Query(default=""), session_id: str = Query(default="default")) -> ToolsResponse:
    """获取当前会话工具定义（含字符数和 token 估算）。"""
    from ..deps import SUBAGENT_LOADER, _MCP_LOADER

    return config_service.tools_preview(
        config_preview_dependencies(),
        username=username,
        workspace=workspace,
        session_id=session_id,
        subagent_loader=SUBAGENT_LOADER,
        mcp_loader=_MCP_LOADER,
    )


@router.get("/settings")
async def get_settings() -> SettingsResponse:
    return config_service.settings_payload(config_mutation_dependencies())


@router.put("/settings")
async def update_settings(body: SettingsUpdateRequest) -> SettingsUpdateResponse:
    return config_service.update_settings(config_mutation_dependencies(), body)


@router.post("/settings/add_model")
async def add_model(body: AddModelRequest) -> AddModelResponse | RouteErrorResponse:
    return config_service.add_model(config_mutation_dependencies(), body)


@router.delete("/settings/remove_model")
async def remove_model(body: RemoveModelRequest) -> RemoveModelResponse | RouteErrorResponse:
    return config_service.remove_model(config_mutation_dependencies(), body)


@router.post("/settings/mcp/add")
async def add_mcp_server(body: McpServerAddRequest) -> McpServerAddResponse | RouteErrorResponse:
    return config_service.add_mcp_server(config_mutation_dependencies(), body)


@router.delete("/settings/mcp/{name}")
async def remove_mcp_server(name: str, username: str = "") -> McpServerRemoveResponse | RouteErrorResponse:
    return config_service.remove_mcp_server(config_mutation_dependencies(), name)
