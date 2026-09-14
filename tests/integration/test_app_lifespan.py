"""The real application lifespan, exercised end to end.

Every other integration test injects ``app.state.session_factory`` directly, so
the lifespan that builds the SQLAlchemy engine and the ARQ Redis pool — and the
``/ready`` path that depends on both — would otherwise never run in a test.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import AppSettings
from backend.db.models import JobOutboxRecord, TicketRecord
from backend.main import create_app
from tests.integration.conftest import REQUESTER_PASSWORD, REQUESTER_USERNAME


@pytest.fixture
async def live_app(
    api_settings: AppSettings,
    seeded_users: None,
) -> AsyncIterator[FastAPI]:
    """Run the application with its real lifespan instead of injected fakes."""
    app = create_app(api_settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def live_client(live_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=live_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_lifespan_builds_the_engine_and_redis_pool(live_app: FastAPI) -> None:
    assert live_app.state.engine is not None
    assert live_app.state.redis is not None
    assert live_app.state.session_factory is not None


async def test_ready_reports_ok_with_real_dependencies(live_client: AsyncClient) -> None:
    response = await live_client.get("/ready")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


async def test_health_stays_live_next_to_readiness(live_client: AsyncClient) -> None:
    response = await live_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_login_and_submission_work_over_the_real_app(
    live_client: AsyncClient,
    live_app: FastAPI,
) -> None:
    login = await live_client.post(
        "/api/auth/login",
        json={"username": REQUESTER_USERNAME, "password": REQUESTER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = await live_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200

    submitted = await live_client.post(
        "/api/tickets",
        json={"user_query": "公司 VPN 无法连接，请查询当前服务状态"},
        headers=headers,
    )
    assert submitted.status_code == 202, submitted.text
    ticket_id = uuid.UUID(submitted.json()["ticket_id"])

    session_factory: async_sessionmaker[AsyncSession] = live_app.state.session_factory
    async with session_factory() as session:
        ticket = await session.get(TicketRecord, ticket_id)
        assert ticket is not None
        assert ticket.status == "queued"
        outbox = (
            await session.execute(
                select(JobOutboxRecord).where(JobOutboxRecord.ticket_id == ticket_id)
            )
        ).scalar_one()
        assert outbox.payload == {"ticket_id": str(ticket_id)}
        assert outbox.published_at is None


async def test_stubbed_routes_still_return_501_over_the_real_app(
    live_client: AsyncClient,
) -> None:
    response = await live_client.get("/api/approvals")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
