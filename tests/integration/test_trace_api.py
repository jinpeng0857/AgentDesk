"""Integration tests for ticket trace API endpoint and redaction guarantees."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import TicketStatus
from backend.db.models import TicketRecord, ToolCallRecord, TraceEventRecord
from backend.db.repositories.tickets import TicketRepository
from tests.integration.conftest import (
    APPROVER_PASSWORD,
    APPROVER_USERNAME,
    REQUESTER_ID,
    REQUESTER_PASSWORD,
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
async def test_trace_api_ordering_isolation_and_redaction(
    authenticated_client: AsyncClient,
    approver_client: AsyncClient,
    unauth_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    now = datetime.now(UTC)

    # 1. Unauthenticated request returns 401
    unauth = await unauth_client.get(f"/api/tickets/{ticket_id}/trace")
    assert unauth.status_code == 401

    # 2. Seed ticket, trace events, and tool call records
    async with session_factory() as session:
        repo = TicketRepository(session)
        t = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id=REQUESTER_ID,
            user_query="Reset my credential please",
            trace_id=trace_id,
            status=TicketStatus.RUNNING.value,
            step_count=2,
        )
        await repo.add(t)

        # Step 1: Supervisor classify
        event_1 = TraceEventRecord(
            request_id="step-1",
            ticket_id=ticket_id,
            trace_id=trace_id,
            actor="supervisor",
            event_type="agent_step",
            summary="classify_and_route",
            timestamp=now - timedelta(seconds=10),
        )
        # Tool call record
        tool_call = ToolCallRecord(
            request_id="req-tool-001",
            ticket_id=ticket_id,
            trace_id=trace_id,
            operation_id=None,
            agent_id="knowledge",
            tool_name="search_knowledge",
            args_summary={"query": "Reset credential", "password": "***"},
            result_summary={"matches": 1},
            outcome="succeeded",
            latency_ms=45,
            retry_count=0,
            timestamp=now - timedelta(seconds=5),
        )
        # Step 2: Knowledge search
        event_2 = TraceEventRecord(
            request_id="step-2",
            ticket_id=ticket_id,
            trace_id=trace_id,
            actor="knowledge",
            event_type="agent_step",
            summary="search_and_ground",
            timestamp=now,
        )

        session.add(event_1)
        session.add(tool_call)
        session.add(event_2)
        await session.commit()

    # 3. Requester gets trace
    response = await authenticated_client.get(f"/api/tickets/{ticket_id}/trace")
    assert response.status_code == 200
    events = response.json()
    assert len(events) == 3

    # Assert time ordering
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)

    # Assert event details
    assert events[0]["request_id"] == "step-1"
    assert events[0]["actor"] == "supervisor"
    assert events[0]["event_type"] == "agent_step"

    assert events[1]["request_id"] == "req-tool-001"
    assert events[1]["actor"] == "knowledge"
    assert events[1]["event_type"] == "tool_call:search_knowledge"

    assert events[2]["request_id"] == "step-2"
    assert events[2]["actor"] == "knowledge"

    # 4. Sensitive value leak check: ensure passwords, tokens, raw emails do not appear
    raw_response_text = response.text
    forbidden_leak_strings = [
        REQUESTER_PASSWORD,
        APPROVER_PASSWORD,
        "Bearer ",
        "token=",
        "password=",
        "password\": \"secret",
    ]
    for forbidden in forbidden_leak_strings:
        assert forbidden not in raw_response_text

    # 5. Requester isolation: Approver gets 404
    foreign_trace = await approver_client.get(f"/api/tickets/{ticket_id}/trace")
    assert foreign_trace.status_code == 404
    assert foreign_trace.json()["error"]["code"] == "TICKET_NOT_FOUND"
