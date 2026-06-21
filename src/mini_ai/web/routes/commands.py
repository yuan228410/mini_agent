"""斜杠命令列表接口"""
from fastapi import APIRouter

from ...application import command_service
from ..route_types import CommandsResponse, McpStatusResponse
from ..runtime_helpers import mcp_status_dependencies

router = APIRouter()


@router.get("/commands")
async def list_commands() -> CommandsResponse:
    return command_service.list_commands()


@router.get("/mcp")
async def mcp_status() -> McpStatusResponse:
    return command_service.mcp_status(mcp_status_dependencies())
