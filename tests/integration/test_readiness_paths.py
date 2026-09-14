"""Every readiness failure branch returns a stable 503, never a crash."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import AppSettings
from backend.main import create_app
from tests.integration.conftest import TEST_JWT_SECRET

UNREACHABLE_DATABASE_URL = (
    "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:1/agentdesk_test"
)


class StubRedis:
    """Stands in for the ARQ pool so a readiness branch can be isolated."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def ping(self) -> bool:
        if self._error is not None:
            raise self._error
        return True


def build_app(
    *,
    jwt_secret: str,
    session_factory: async_sessionmaker[AsyncSession] | None,
    redis: object | None,
    database_url: str,
) -> FastAPI:
    settings = AppSettings(
        database_url=database_url,
        redis_url="redis://localhost:6379/15",
        jwt_secret=jwt_secret,
        openai_model="test-model",
    )
    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.redis = redis
    return app


async def read_ready(app: FastAPI) -> tuple[int, dict[str, object]]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    return response.status_code, response.json()


@pytest.fixture
async def healthy_session_factory(
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_ready_succeeds_with_working_dependencies(
    healthy_session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> None:
    app = build_app(
        jwt_secret=TEST_JWT_SECRET,
        session_factory=healthy_session_factory,
        redis=StubRedis(),
        database_url=test_database_url,
    )

    status, body = await read_ready(app)

    assert status == 200
    assert body == {"status": "ok"}


async def test_ready_rejects_a_placeholder_jwt_secret(
    healthy_session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> None:
    app = build_app(
        jwt_secret="replace-with-at-least-32-random-bytes",
        session_factory=healthy_session_factory,
        redis=StubRedis(),
        database_url=test_database_url,
    )

    status, body = await read_ready(app)

    assert status == 503
    assert body["error"]["code"] == "NOT_READY"


async def test_ready_rejects_a_short_jwt_secret(
    healthy_session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> None:
    app = build_app(
        jwt_secret="too-short",
        session_factory=healthy_session_factory,
        redis=StubRedis(),
        database_url=test_database_url,
    )

    status, body = await read_ready(app)

    assert status == 503
    assert body["error"]["code"] == "NOT_READY"


async def test_ready_reports_503_when_postgres_is_unreachable() -> None:
    broken_engine = create_async_engine(
        UNREACHABLE_DATABASE_URL,
        poolclass=NullPool,
        connect_args={"timeout": 2},
    )
    try:
        app = build_app(
            jwt_secret=TEST_JWT_SECRET,
            session_factory=async_sessionmaker(broken_engine, expire_on_commit=False),
            redis=StubRedis(),
            database_url=UNREACHABLE_DATABASE_URL,
        )

        status, body = await read_ready(app)
    finally:
        await broken_engine.dispose()

    assert status == 503
    assert body["error"]["code"] == "NOT_READY"


async def test_ready_reports_503_when_the_redis_pool_is_absent(
    healthy_session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> None:
    app = build_app(
        jwt_secret=TEST_JWT_SECRET,
        session_factory=healthy_session_factory,
        redis=None,
        database_url=test_database_url,
    )

    status, body = await read_ready(app)

    assert status == 503
    assert body["error"]["code"] == "NOT_READY"


async def test_ready_reports_503_when_redis_is_unreachable(
    healthy_session_factory: async_sessionmaker[AsyncSession], test_database_url: str
) -> None:
    app = build_app(
        jwt_secret=TEST_JWT_SECRET,
        session_factory=healthy_session_factory,
        redis=StubRedis(ConnectionError("redis is down")),
        database_url=test_database_url,
    )

    status, body = await read_ready(app)

    assert status == 503
    assert body["error"]["code"] == "NOT_READY"


async def test_ready_reports_503_when_no_session_factory_is_configured(
    test_database_url: str,
) -> None:
    app = build_app(
        jwt_secret=TEST_JWT_SECRET,
        session_factory=None,
        redis=StubRedis(),
        database_url=test_database_url,
    )

    status, body = await read_ready(app)

    assert status == 503
    assert body["error"]["code"] == "NOT_READY"
