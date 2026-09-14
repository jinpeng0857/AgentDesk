"""Password hashing and JWT claim contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from pydantic import SecretStr

from agents.schemas import ActorContext
from backend.auth.jwt import AuthenticationError, decode_access_token, issue_access_token
from backend.auth.passwords import hash_password, verify_password
from backend.config import AppSettings

TEST_SECRET = "unit-test-secret-with-at-least-32-bytes"


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/agentdesk_test",
        redis_url="redis://localhost:6379/15",
        jwt_secret=TEST_SECRET,
    )


def _encode(settings: AppSettings, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "user-requester-001",
        "role": "requester",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": "test-jti",
    }
    claims.update(overrides)
    return pyjwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def test_password_hash_never_contains_plaintext() -> None:
    password = SecretStr("correct-horse-battery-staple")
    encoded = hash_password(password)

    assert password.get_secret_value() not in encoded
    assert verify_password(password, encoded)


def test_wrong_password_is_rejected() -> None:
    encoded = hash_password(SecretStr("correct-horse-battery-staple"))

    assert verify_password(SecretStr("wrong-password"), encoded) is False


def test_unknown_hash_is_rejected_without_raising() -> None:
    assert verify_password(SecretStr("anything"), "not-a-valid-hash") is False


def test_issued_token_decodes_to_the_same_actor(settings: AppSettings) -> None:
    actor = ActorContext(user_id="user-requester-001", role="requester", authenticated=True)

    response = issue_access_token(actor, settings)
    decoded = decode_access_token(response.access_token, settings)

    assert response.token_type == "bearer"
    assert response.expires_in_s == settings.jwt_ttl_s
    assert decoded == actor


def test_expired_token_is_rejected(settings: AppSettings) -> None:
    token = _encode(settings, exp=int((datetime.now(UTC) - timedelta(seconds=1)).timestamp()))

    with pytest.raises(AuthenticationError, match="TOKEN_EXPIRED"):
        decode_access_token(token, settings)


def test_wrong_issuer_is_rejected(settings: AppSettings) -> None:
    token = _encode(settings, iss="somebody-else")

    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        decode_access_token(token, settings)


def test_wrong_audience_is_rejected(settings: AppSettings) -> None:
    token = _encode(settings, aud="another-api")

    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        decode_access_token(token, settings)


def test_token_signed_with_another_secret_is_rejected(settings: AppSettings) -> None:
    now = datetime.now(UTC)
    token = pyjwt.encode(
        {
            "sub": "user-requester-001",
            "role": "requester",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": "forged",
        },
        "attacker-controlled-secret-of-sufficient-length",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        decode_access_token(token, settings)


def test_token_without_jti_is_rejected(settings: AppSettings) -> None:
    token = _encode(settings)
    claims = pyjwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    del claims["jti"]
    reissued = pyjwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm="HS256"
    )

    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        decode_access_token(reissued, settings)


def test_unsupported_role_is_rejected(settings: AppSettings) -> None:
    token = _encode(settings, role="administrator")

    with pytest.raises(AuthenticationError, match="TOKEN_INVALID"):
        decode_access_token(token, settings)
