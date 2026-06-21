"""会话 REST API — CRUD + 列表 + 重命名 + 待办"""
import time

from fastapi import APIRouter, Query

from ...application import session_service
from ...logger import logger
from ..route_types import (
    RouteErrorResponse,
    RouteOkResponse,
    SessionBatchDeleteRequest,
    SessionBatchDeleteResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDeleteRequest,
    SessionListResponse,
    SessionRenameRequest,
    SessionRenameResponse,
    TodosResponse,
)
from ..runtime_helpers import session_service_dependencies

router = APIRouter()


@router.post("/session")
async def create_session(body: SessionCreateRequest) -> SessionCreateResponse | RouteErrorResponse:
    return session_service.create_session(session_service_dependencies(), body)


@router.get("/sessions")
async def list_sessions(username: str = Query(...), workspace: str | None = Query(default=None)) -> SessionListResponse:
    _t0 = time.time()
    result = session_service.list_sessions(session_service_dependencies(), username, workspace)
    logger.debug(f"[perf] list_sessions ws={workspace} count={len(result['sessions'])} time={time.time()-_t0:.3f}s")
    return result


@router.get("/todos")
async def get_todos(username: str = Query(...), workspace: str | None = Query(default=None), session_id: str = Query(...)) -> TodosResponse:
    return session_service.get_todos(session_service_dependencies(), username, workspace, session_id)


@router.delete("/session")
async def delete_session(body: SessionDeleteRequest) -> RouteOkResponse | RouteErrorResponse:
    return session_service.delete_session(session_service_dependencies(), body)


@router.post("/sessions/batch_delete")
async def batch_delete_sessions(body: SessionBatchDeleteRequest) -> SessionBatchDeleteResponse | RouteErrorResponse:
    return session_service.batch_delete_sessions(session_service_dependencies(), body)


@router.patch("/session/rename")
async def rename_session(body: SessionRenameRequest) -> SessionRenameResponse | RouteErrorResponse:
    return session_service.rename_session(session_service_dependencies(), body)
