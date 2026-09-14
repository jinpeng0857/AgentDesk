"""Process health API contract routes."""

from fastapi import APIRouter

from backend.api.schemas import ErrorEnvelope, HealthResponse
from backend.api.stubs import raise_not_ready

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={503: {"model": ErrorEnvelope, "description": "Dependencies not ready"}},
)
async def readiness() -> HealthResponse:
    raise_not_ready()
