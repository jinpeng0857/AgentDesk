"""Authentication API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.api.dependencies import SessionDep, SettingsDep
from backend.api.schemas import ActorResponse, ErrorEnvelope, LoginRequest, TokenResponse
from backend.auth.dependencies import CurrentActor
from backend.auth.jwt import AuthenticationError, build_actor, issue_access_token
from backend.auth.passwords import verify_password
from backend.db.repositories.users import UserRepository

router = APIRouter(prefix="/api/auth", tags=["auth"])

AUTH_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"}
}


@router.post("/login", response_model=TokenResponse, responses=AUTH_ERROR_RESPONSES)
async def login(
    payload: LoginRequest,
    settings: SettingsDep,
    session: SessionDep,
) -> TokenResponse:
    user = await UserRepository(session).get_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        # Unknown username and wrong password must be indistinguishable.
        raise AuthenticationError(
            code="INVALID_CREDENTIALS", message="Username or password is incorrect"
        )
    if not user.active:
        raise AuthenticationError(code="ACCOUNT_INACTIVE", message="Account is inactive")
    return issue_access_token(build_actor(user.id, user.role), settings)


@router.get("/me", response_model=ActorResponse, responses=AUTH_ERROR_RESPONSES)
async def current_actor(actor: CurrentActor) -> ActorResponse:
    return ActorResponse(user_id=actor.user_id, role=actor.role)
