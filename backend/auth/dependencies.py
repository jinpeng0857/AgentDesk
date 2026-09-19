"""Dependency that resolves the authenticated actor from a bearer token."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agents.schemas import ActorContext
from backend.api.dependencies import SessionDep, SettingsDep
from backend.api.errors import ApiError
from backend.auth.jwt import AuthenticationError, decode_access_token
from backend.db.repositories.users import UserRepository


def bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError(
            code="MISSING_BEARER_TOKEN", message="A bearer token is required"
        )
    return token


async def get_current_actor(
    request: Request,
    settings: SettingsDep,
    session: SessionDep,
) -> ActorContext:
    actor = decode_access_token(bearer_token(request), settings)
    user = await UserRepository(session).get_by_id(actor.user_id)
    if user is None or not user.active:
        raise AuthenticationError(
            code="ACCOUNT_INACTIVE", message="Account is missing or inactive"
        )
    return actor


CurrentActor = Annotated[ActorContext, Depends(get_current_actor)]


def require_approver(actor: CurrentActor) -> ActorContext:
    if actor.role != "approver":
        raise ApiError(
            status_code=403,
            code="FORBIDDEN",
            message="Only approvers can access approvals",
        )
    return actor


ApproverActor = Annotated[ActorContext, Depends(require_approver)]

