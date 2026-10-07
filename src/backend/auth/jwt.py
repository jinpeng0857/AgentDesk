"""JWT issuing and strict claim validation.

Every claim the API relies on is required at decode time, so a token minted by a
different issuer, audience or signing key can never become an authenticated
actor.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import jwt as pyjwt

from agents.schemas import ActorContext
from backend.api.schemas import TokenResponse
from backend.config import AppSettings

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("sub", "role", "iss", "aud", "iat", "exp", "jti")
ALLOWED_ROLES = ("requester", "approver")
Role = Literal["requester", "approver"]


class AuthenticationError(Exception):
    """A token or credential failure that maps to HTTP 401."""

    status_code = 401

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def build_actor(user_id: str, role: str) -> ActorContext:
    if role not in ALLOWED_ROLES:
        raise AuthenticationError(
            code="TOKEN_INVALID", message="Account role is not supported"
        )
    return ActorContext(
        user_id=user_id,
        role=cast(Role, role),
        authenticated=True,
    )


def issue_access_token(actor: ActorContext, settings: AppSettings) -> TokenResponse:
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.jwt_ttl_s)
    claims = {
        "sub": actor.user_id,
        "role": actor.role,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = pyjwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM
    )
    return TokenResponse(access_token=token, expires_in_s=settings.jwt_ttl_s)


def decode_access_token(token: str, settings: AppSettings) -> ActorContext:
    try:
        claims = pyjwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise AuthenticationError(
            code="TOKEN_EXPIRED", message="Access token has expired"
        ) from exc
    except pyjwt.InvalidTokenError as exc:
        raise AuthenticationError(
            code="TOKEN_INVALID", message="Access token is not valid"
        ) from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError(
            code="TOKEN_INVALID", message="Access token has no subject"
        )
    return build_actor(subject, str(claims.get("role")))
