"""Stable failures for contract-only Phase 0 routes."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.api.schemas import ErrorDetail, ErrorEnvelope


class ContractStubError(Exception):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


STUB_RESPONSE: dict[int | str, dict[str, Any]] = {
    501: {"model": ErrorEnvelope, "description": "Contract only"}
}


def raise_not_implemented() -> NoReturn:
    raise ContractStubError(
        status_code=501,
        code="NOT_IMPLEMENTED",
        message="This endpoint is a Phase 0 contract and has no business implementation.",
    )


def raise_not_ready() -> NoReturn:
    raise ContractStubError(
        status_code=503,
        code="NOT_READY",
        message="Runtime dependencies are introduced in Phase 1.",
    )


async def contract_stub_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, ContractStubError):
        raise exc
    request_id = request.headers.get("x-request-id", "contract-only")
    content = ErrorEnvelope(
        error=ErrorDetail(
            code=exc.code,
            message=exc.message,
            request_id=request_id,
        )
    )
    return JSONResponse(status_code=exc.status_code, content=content.model_dump(mode="json"))
