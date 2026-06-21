"""文件浏览与预览 API"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from ...application import file_service
from ..route_types import (
    BrowseDirsResponse,
    FileListResponse,
    FileReadBinaryResponse,
    FileReadTextResponse,
    FileSearchResponse,
    RouteErrorResponse,
)
from ..runtime_helpers import workspace_manager_for_user

router = APIRouter()
_WEB_CWD = Path.cwd()


def _workspace_manager(username: str):
    return workspace_manager_for_user(username)


@router.get("/files/list")
async def list_files(
    path: str = Query(default=""),
    workspace: str = Query(default=""),
    username: str = Query(...),
) -> FileListResponse | RouteErrorResponse:
    return await asyncio.to_thread(
        file_service.list_files,
        _workspace_manager(username),
        workspace=workspace,
        path=path,
        fallback_cwd=_WEB_CWD,
    )


@router.get("/files/read")
async def read_file(
    path: str = Query(...),
    workspace: str = Query(default=""),
    username: str = Query(...),
    offset: int = Query(default=0),
    limit: int = Query(default=200),
) -> FileReadTextResponse | FileReadBinaryResponse | RouteErrorResponse:
    return await asyncio.to_thread(
        file_service.read_file,
        _workspace_manager(username),
        workspace=workspace,
        path=path,
        offset=offset,
        limit=limit,
        fallback_cwd=_WEB_CWD,
    )


@router.get("/files/raw")
async def raw_file(
    path: str = Query(...),
    workspace: str = Query(default=""),
    username: str = Query(...),
):
    """返回原始文件内容（用于图片预览、下载等）。"""
    result = await asyncio.to_thread(
        file_service.raw_file,
        _workspace_manager(username),
        workspace=workspace,
        path=path,
        fallback_cwd=_WEB_CWD,
    )
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=int(result.get("status_code", 404)))
    return FileResponse(result["path"], media_type=result["media_type"])


@router.get("/files/search")
async def search_files(
    query: str = Query(...),
    path: str = Query(default=""),
    workspace: str = Query(default=""),
    username: str = Query(...),
) -> FileSearchResponse | RouteErrorResponse:
    """递归搜索文件名。"""
    return await asyncio.to_thread(
        file_service.search_files,
        _workspace_manager(username),
        workspace=workspace,
        path=path,
        query=query,
        fallback_cwd=_WEB_CWD,
    )


@router.get("/files/browse")
async def browse_dirs(path: str = Query(default=""), username: str = Query(...)) -> BrowseDirsResponse | RouteErrorResponse:
    return await asyncio.to_thread(file_service.browse_dirs, path)
