"""Ticket submission writes the ticket and its outbox row in one transaction."""

from __future__ import annotations

import sys
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import JobOutboxRecord, TicketRecord
from backend.db.repositories.outbox import OutboxRepository
from tests.integration.conftest import REQUESTER_ID

TICKETS = "/api/tickets"


async def _outbox_rows(
    session_factory: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID
) -> list[JobOutboxRecord]:
    async with session_factory() as session:
        result = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.ticket_id == ticket_id)
        )
        return list(result.scalars().all())


async def test_submitting_a_ticket_returns_202_and_queues_it(
    authenticated_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await authenticated_client.post(
        TICKETS, json={"user_query": "公司 VPN 无法连接，请查询当前服务状态"}
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    ticket_id = uuid.UUID(body["ticket_id"])

    async with session_factory() as session:
        ticket = await session.get(TicketRecord, ticket_id)
        assert ticket is not None
        assert ticket.thread_id == body["ticket_id"]
        assert ticket.status == "queued"
        assert ticket.requester_id == REQUESTER_ID

    rows = await _outbox_rows(session_factory, ticket_id)
    assert len(rows) == 1
    assert rows[0].job_name == "start_ticket"
    assert rows[0].payload == {"ticket_id": str(ticket_id)}
    assert rows[0].dedupe_key == f"start:{ticket_id}"
    assert rows[0].published_at is None


async def test_queue_payload_never_carries_the_user_query(
    authenticated_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    secret_query = "private-network-detail-should-not-be-queued"
    response = await authenticated_client.post(TICKETS, json={"user_query": secret_query})
    ticket_id = uuid.UUID(response.json()["ticket_id"])

    rows = await _outbox_rows(session_factory, ticket_id)

    assert set(rows[0].payload) == {"ticket_id"}
    assert secret_query not in str(rows[0].payload)


async def test_submission_never_loads_graph_execution(
    authenticated_client: AsyncClient,
) -> None:
    sys.modules.pop("backend.graph", None)
    sys.modules.pop("backend.graph.builder", None)

    response = await authenticated_client.post(TICKETS, json={"user_query": "VPN down"})

    assert response.status_code == 202
    assert "backend.graph.builder" not in sys.modules


async def test_unauthenticated_submission_is_rejected(api_client: AsyncClient) -> None:
    response = await api_client.post(TICKETS, json={"user_query": "VPN down"})

    assert response.status_code == 401


async def test_outbox_failure_rolls_back_the_ticket(
    authenticated_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _explode(
        self: OutboxRepository, *, ticket_id: uuid.UUID, dedupe_key: str
    ) -> JobOutboxRecord:
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr(OutboxRepository, "enqueue_start_ticket", _explode)

    rollback_marker = "rollback-probe-query-must-not-persist"
    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await authenticated_client.post(TICKETS, json={"user_query": rollback_marker})

    async with session_factory() as session:
        remaining = (await session.execute(select(TicketRecord))).scalars().all()
    assert all(record.user_query != rollback_marker for record in remaining)
