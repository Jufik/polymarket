"""Structured error responses for the API."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    """Raise from any route to return a structured JSON error."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        detail: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    body: dict = {"error": {"code": exc.code, "message": exc.message}}
    if exc.detail:
        body["error"]["detail"] = exc.detail
    return JSONResponse(status_code=exc.status, content=body)
