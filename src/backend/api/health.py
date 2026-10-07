"""Process health and readiness routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.api.dependencies import SessionDep, get_settings
from backend.api.schemas import ErrorEnvelope, HealthResponse
from backend.api.stubs import raise_not_ready
from backend.config import ConfigurationError

router = APIRouter(tags=["health"])

READINESS_ERRORS: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorEnvelope, "description": "Dependencies are not ready"}
}


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, responses=READINESS_ERRORS)
async def readiness(request: Request, session: SessionDep) -> HealthResponse:
    settings = get_settings(request)
    try:
        settings.validate_readiness(require_model=False)
    except ConfigurationError as exc:
        raise_not_ready(f"Configuration is not ready: {exc}")

    try:
        await session.execute(text("select 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        # An unreachable host surfaces as a driver timeout or OSError rather than
        # a SQLAlchemyError, and readiness must still answer 503 instead of
        # crashing with an unhandled exception.
        raise_not_ready("PostgreSQL is not reachable")

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise_not_ready("Redis pool has not been created")
    try:
        await redis.ping()
    except (RedisError, OSError, TimeoutError):
        raise_not_ready("Redis is not reachable")

    return HealthResponse(status="ok")
