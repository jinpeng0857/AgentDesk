"""Integration tests for ticket query API endpoints: list and detail."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import Intent, ResolutionMode, RiskLevel, TicketStatus
from backend.db.models import TicketKnowledgeRefRecord, TicketRecord
from backend.db.repositories.tickets import TicketRepository
from tests.integration.conftest import (
    APPROVER_PASSWORD,
    APPROVER_USERNAME,
    REQUESTER_ID,
)


@pytest.fixture
async def unauth_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def approver_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": APPROVER_USERNAME, "password": APPROVER_PASSWORD},
        )
        assert login.status_code == 200
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield client


@pytest.mark.asyncio
async def test_ticket_queries_requester_isolation_and_detail(
    authenticated_client: AsyncClient,
    approver_client: AsyncClient,
    unauth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    # 1. Unauthenticated requests to list or get return 401
    list_unauth = await unauth_client.get("/api/tickets")
    assert list_unauth.status_code == 401
    assert list_unauth.json()["error"]["code"] == "MISSING_BEARER_TOKEN"

    ticket_id_1 = uuid.uuid4()
    trace_id_1 = uuid.uuid4()
    ticket_id_2 = uuid.uuid4()
    trace_id_2 = uuid.uuid4()

    # 2. Seed two tickets for REQUESTER_ID
    async with session_factory() as session:
        repo = TicketRepository(session)
        t1 = TicketRecord(
            id=ticket_id_1,
            thread_id=str(ticket_id_1),
            requester_id=REQUESTER_ID,
            user_query="VPN issue 1",
            trace_id=trace_id_1,
            status=TicketStatus.RESOLVED.value,
            intent=Intent.NETWORK.value,
            risk_level=RiskLevel.LOW.value,
            resolution_mode=ResolutionMode.AUTOMATED.value,
            final_response="Restart AnyConnect client.",
            step_count=3,
        )
        t2 = TicketRecord(
            id=ticket_id_2,
            thread_id=str(ticket_id_2),
            requester_id=REQUESTER_ID,
            user_query="Access request issue",
            trace_id=trace_id_2,
            status=TicketStatus.QUEUED.value,
            step_count=0,
        )
        await repo.add(t1)
        await repo.add(t2)

        ref = TicketKnowledgeRefRecord(
            ticket_id=ticket_id_1,
            position=0,
            document_id="DOC-VPN-001",
            chunk_id="chunk-1",
            title="VPN Guide",
            content_excerpt="Troubleshoot steps here",
            score=0.95,
        )
        session.add(ref)
        await session.commit()

    # 3. List tickets as REQUESTER
    list_resp = await authenticated_client.get("/api/tickets")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) >= 2
    ticket_ids = [item["ticket_id"] for item in items]
    assert str(ticket_id_1) in ticket_ids
    assert str(ticket_id_2) in ticket_ids

    # 4. Detail endpoint as REQUESTER exposes citation 5 fields
    detail_resp = await authenticated_client.get(f"/api/tickets/{ticket_id_1}")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["ticket_id"] == str(ticket_id_1)
    assert detail_data["requester_id"] == REQUESTER_ID
    assert detail_data["status"] == "resolved"
    assert detail_data["intent"] == "network"
    assert detail_data["resolution_mode"] == "automated"
    assert detail_data["final_response"] == "Restart AnyConnect client."

    refs = detail_data["knowledge_refs"]
    assert len(refs) == 1
    assert set(refs[0].keys()) == {
        "document_id",
        "chunk_id",
        "title",
        "content_excerpt",
        "score",
    }
    assert refs[0]["document_id"] == "DOC-VPN-001"
    assert refs[0]["chunk_id"] == "chunk-1"
    assert refs[0]["score"] == 0.95

    # 5. Other user (approver) cannot view requester's ticket -> returns 404 (not 403)
    foreign_get = await approver_client.get(f"/api/tickets/{ticket_id_1}")
    assert foreign_get.status_code == 404
    assert foreign_get.json()["error"]["code"] == "TICKET_NOT_FOUND"

    # Approver list should not include requester's tickets
    approver_list = await approver_client.get("/api/tickets")
    assert approver_list.status_code == 200
    approver_ticket_ids = [item["ticket_id"] for item in approver_list.json()]
    assert str(ticket_id_1) not in approver_ticket_ids
    assert str(ticket_id_2) not in approver_ticket_ids

    # 6. Invalid UUID string returns 404
    invalid_resp = await authenticated_client.get("/api/tickets/not-a-valid-uuid")
    assert invalid_resp.status_code == 404
    assert invalid_resp.json()["error"]["code"] == "TICKET_NOT_FOUND"
