"""Thin REST adapters for chat routes."""
from __future__ import annotations

from typing import Any

from ..application import chat_service
from .chat_export_response import chat_export_response
from .chat_dependencies import chat_rest_dependencies


def chat_history_response(*, session_id: str = "", username: str, workspace: str = "") -> dict[str, Any]:
    """Return chat history using route-level primitives."""

    return chat_service.chat_history(chat_rest_dependencies(), session_id=session_id, username=username, workspace=workspace)


def chat_reset_response(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reset chat using route-level primitives."""

    return chat_service.reset_chat(chat_rest_dependencies(), body)


def chat_export_route_response(
    *,
    session_id: str = "",
    username: str,
    workspace: str = "",
    limit: int = 0,
    include_thinking: bool = False,
    include_tools: bool = False,
):
    """Return a FastAPI export response from route-level primitives."""

    result = chat_service.export_chat(
        chat_rest_dependencies(),
        session_id=session_id,
        username=username,
        workspace=workspace,
        limit=limit,
        include_thinking=include_thinking,
        include_tools=include_tools,
    )
    return chat_export_response(result)
