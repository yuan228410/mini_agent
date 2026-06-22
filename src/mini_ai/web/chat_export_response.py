"""Web chat export response adapter."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi.responses import JSONResponse, Response

from ..application.chat_service import ChatExportResult


def chat_export_response(result: ChatExportResult | dict[str, Any]) -> Response | JSONResponse:
    """Convert an application export result to a FastAPI response."""

    if isinstance(result, dict):
        status_code = int(result.get("status_code", 400))
        return JSONResponse({"error": result.get("error", "导出失败")}, status_code=status_code)

    encoded_name = quote(result.filename)
    return Response(
        content=result.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}.md"},
    )
