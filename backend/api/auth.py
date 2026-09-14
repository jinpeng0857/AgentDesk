"""Authentication API contract routes."""

from fastapi import APIRouter

from backend.api.schemas import (
    ActorResponse,
    LoginRequest,
    TokenResponse,
)
from backend.api.stubs import STUB_RESPONSE, raise_not_implemented

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, responses=STUB_RESPONSE)
async def login(_: LoginRequest) -> TokenResponse:
    raise_not_implemented()


@router.get("/me", response_model=ActorResponse, responses=STUB_RESPONSE)
async def current_actor() -> ActorResponse:
    raise_not_implemented()
