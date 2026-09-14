"""Real authentication behaviour against PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import AsyncClient

from backend.config import AppSettings
from tests.integration.conftest import (
    INACTIVE_PASSWORD,
    INACTIVE_USERNAME,
    REQUESTER_ID,
    REQUESTER_PASSWORD,
    REQUESTER_USERNAME,
    TEST_JWT_SECRET,
)

LOGIN = "/api/auth/login"
ME = "/api/auth/me"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_login_then_me_returns_fixed_actor(api_client: AsyncClient) -> None:
    login = await api_client.post(
        LOGIN, json={"username": REQUESTER_USERNAME, "password": REQUESTER_PASSWORD}
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    me = await api_client.get(ME, headers=_bearer(token))

    assert me.status_code == 200
    assert me.json() == {"user_id": REQUESTER_ID, "role": "requester"}


async def test_unknown_username_and_wrong_password_are_indistinguishable(
    api_client: AsyncClient,
) -> None:
    unknown = await api_client.post(
        LOGIN, json={"username": "nobody", "password": REQUESTER_PASSWORD}
    )
    wrong = await api_client.post(
        LOGIN, json={"username": REQUESTER_USERNAME, "password": "not-the-password"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_inactive_user_cannot_log_in(api_client: AsyncClient) -> None:
    response = await api_client.post(
        LOGIN, json={"username": INACTIVE_USERNAME, "password": INACTIVE_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"


async def test_missing_bearer_token_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.get(ME)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "MISSING_BEARER_TOKEN"


async def test_expired_token_is_rejected_with_token_expired(
    api_client: AsyncClient, api_settings: AppSettings
) -> None:
    now = datetime.now(UTC)
    expired = pyjwt.encode(
        {
            "sub": REQUESTER_ID,
            "role": "requester",
            "iss": api_settings.jwt_issuer,
            "aud": api_settings.jwt_audience,
            "iat": int((now - timedelta(minutes=30)).timestamp()),
            "exp": int((now - timedelta(minutes=10)).timestamp()),
            "jti": "expired-jti",
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )

    response = await api_client.get(ME, headers=_bearer(expired))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_token_for_unknown_user_is_rejected(
    api_client: AsyncClient, api_settings: AppSettings
) -> None:
    now = datetime.now(UTC)
    orphan = pyjwt.encode(
        {
            "sub": "user-that-does-not-exist",
            "role": "requester",
            "iss": api_settings.jwt_issuer,
            "aud": api_settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": "orphan-jti",
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )

    response = await api_client.get(ME, headers=_bearer(orphan))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"


async def test_error_envelope_carries_a_request_id(api_client: AsyncClient) -> None:
    response = await api_client.get(ME, headers={"x-request-id": "trace-me-123"})

    assert response.status_code == 401
    assert response.json()["error"]["request_id"] == "trace-me-123"


@pytest.mark.parametrize("password", ["", " "])
async def test_blank_password_fails_authentication(
    api_client: AsyncClient, password: str
) -> None:
    """The published contract allows any password string, so blanks fail auth, not validation."""
    response = await api_client.post(
        LOGIN, json={"username": REQUESTER_USERNAME, "password": password}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
