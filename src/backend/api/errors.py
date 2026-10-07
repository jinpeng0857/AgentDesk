"""Typed API errors and the JSON envelope handlers."""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.api.schemas import ErrorDetail, ErrorEnvelope
from backend.auth.jwt import AuthenticationError


class ApiError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def request_id_for(request: Request) -> str:
    return request.headers.get("x-request-id") or uuid.uuid4().hex


def error_response(
    request: Request, *, status_code: int, code: str, message: str
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message[:1_000] or code,
            request_id=request_id_for(request),
        )
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):
        raise exc
    return error_response(
        request, status_code=exc.status_code, code=exc.code, message=exc.message
    )


async def authentication_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AuthenticationError):
        raise exc
    return error_response(
        request, status_code=exc.status_code, code=exc.code, message=exc.message
    )
