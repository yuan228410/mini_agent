"""斜杠命令列表接口"""
from fastapi import APIRouter

from ...application import command_service
from ..route_types import CommandsResponse, McpStatusResponse

router = APIRouter()


@router.get("/commands")
async def list_commands() -> CommandsResponse:
    return command_service.list_commands()


@router.get("/mcp")
async def mcp_status() -> McpStatusResponse:
    from ..deps import MCP_SETTINGS, _MCP_LOADER

    return command_service.mcp_status(MCP_SETTINGS, _MCP_LOADER)
