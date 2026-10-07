"""Production gateway wiring: real adapters, real audit, verified zero writes.

The unit suite proves the gateway's ordering with fakes. This module wires the
same executor to the real SQL adapters, the fail-closed write adapters and the
PostgreSQL audit sink, so the phase 1 zero-write guarantee is asserted against
the adapters that would actually run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import ToolOutcome
from backend.db.models import (
    OrderRecord,
    ServiceStatusRecord,
    TicketKnowledgeRefRecord,
    TicketRecord,
    ToolCallRecord,
    TraceEventRecord,
    UserRecord,
)
from backend.db.repositories.audit import AuditRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.executor import ReadToolExecutor, ToolAdapters
from backend.integrations.knowledge_store import ChromaKnowledgeStore
from backend.integrations.tool_adapters import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledRefundService,
    DisabledTicketService,
    SqlOrderRepository,
    SqlServiceRepository,
    SqlUserRepository,
)
from scripts.maintenance.seed_demo_data import seed_knowledge
from tests.integration.conftest import REQUESTER_ID

WRITE_TOOLS = ("create_ticket", "request_access", "reset_password", "process_refund")
COUNTED_TABLES = (
    UserRecord,
    OrderRecord,
    ServiceStatusRecord,
    TicketKnowledgeRefRecord,
    TraceEventRecord,
)


async def seed_ticket(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query="公司 VPN 无法连接",
                trace_id=trace_id,
                status="queued",
                step_count=0,
            )
        )
        await session.commit()
    return ticket_id, trace_id


async def business_row_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with session_factory() as session:
        for model in COUNTED_TABLES:
            total = await session.scalar(select(func.count()).select_from(model))
            counts[model.__tablename__] = int(total or 0)
    return counts


async def audit_rows(
    session_factory: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID
) -> list[ToolCallRecord]:
    async with session_factory() as session:
        result = await session.execute(
            select(ToolCallRecord)
            .where(ToolCallRecord.ticket_id == ticket_id)
            .order_by(ToolCallRecord.timestamp, ToolCallRecord.id)
        )
        return list(result.scalars().all())


@asynccontextmanager
async def build_executor(
    session_factory: async_sessionmaker[AsyncSession],
    chroma_path: Path,
    ticket_id: uuid.UUID,
    trace_id: uuid.UUID,
) -> AsyncIterator[ReadToolExecutor]:
    async with session_factory() as session:
        yield ReadToolExecutor(
            adapters=ToolAdapters(
                knowledge=ChromaKnowledgeStore(chroma_path),
                user=SqlUserRepository(session),
                order=SqlOrderRepository(session),
                service=SqlServiceRepository(session),
                credential=DisabledCredentialService(),
                refund=DisabledRefundService(),
                ticket=DisabledTicketService(),
                access_request=DisabledAccessRequestService(),
            ),
            audit=SqlAuditSink(AuditRepository(session)),
            ticket_id=ticket_id,
            trace_id=trace_id,
        )
        await session.commit()


@pytest.fixture
def chroma_path(tmp_path: Path) -> Path:
    seed_knowledge(tmp_path)
    return tmp_path


async def test_every_write_tool_is_refused_with_zero_side_effects(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_demo_data: None,
    chroma_path: Path,
) -> None:
    ticket_id, trace_id = await seed_ticket(session_factory)
    before = await business_row_counts(session_factory)

    async with build_executor(session_factory, chroma_path, ticket_id, trace_id) as executor:
        results = [
            await executor.execute(agent_id="action", tool_name=tool, raw_args={})
            for tool in WRITE_TOOLS
        ]

    assert all(result.outcome is ToolOutcome.REJECTED for result in results)
    assert all(result.error_code == "WRITE_CAPABILITY_DISABLED" for result in results)
    assert all(result.data is None for result in results)

    # The adapters that would mutate data were never reached.
    assert await business_row_counts(session_factory) == before

    rows = await audit_rows(session_factory, ticket_id)
    assert len(rows) == len(WRITE_TOOLS)
    assert {row.tool_name for row in rows} == set(WRITE_TOOLS)
    assert {row.outcome for row in rows} == {"rejected"}
    assert all(row.agent_id == "action" for row in rows)


async def test_unknown_tool_is_refused_and_persisted_to_the_audit_table(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_demo_data: None,
    chroma_path: Path,
) -> None:
    ticket_id, trace_id = await seed_ticket(session_factory)

    async with build_executor(session_factory, chroma_path, ticket_id, trace_id) as executor:
        result = await executor.execute(
            agent_id="action", tool_name="delete_user", raw_args={"user_id": "u"}
        )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "UNKNOWN_TOOL"

    rows = await audit_rows(session_factory, ticket_id)
    assert [row.tool_name for row in rows] == ["delete_user"]


async def test_real_knowledge_read_is_executed_and_audited_with_h18_fields(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_demo_data: None,
    chroma_path: Path,
) -> None:
    ticket_id, trace_id = await seed_ticket(session_factory)

    async with build_executor(session_factory, chroma_path, ticket_id, trace_id) as executor:
        result = await executor.execute(
            agent_id="knowledge",
            tool_name="search_knowledge",
            raw_args={"query": "公司 VPN 无法连接", "top_k": 3},
        )

    assert result.outcome is ToolOutcome.SUCCEEDED
    assert result.data is not None
    assert result.data.kind == "search_knowledge"
    assert result.operation_id is None, "read tools own no write operation"

    rows = await audit_rows(session_factory, ticket_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "search_knowledge"
    assert row.outcome == "succeeded"
    assert row.operation_id is None
    assert row.ticket_id == ticket_id
    assert row.trace_id == trace_id
    assert row.latency_ms >= 0
    assert row.retry_count == 0
    assert row.request_id
    assert row.args_summary["query"] == "公司 VPN 无法连接"


async def test_real_order_read_returns_a_decimal_and_is_audited(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_demo_data: None,
    chroma_path: Path,
) -> None:
    ticket_id, trace_id = await seed_ticket(session_factory)

    async with build_executor(session_factory, chroma_path, ticket_id, trace_id) as executor:
        result = await executor.execute(
            agent_id="action", tool_name="query_order", raw_args={"order_id": "ORD-1001"}
        )

    assert result.outcome is ToolOutcome.SUCCEEDED
    assert result.data is not None
    assert result.data.kind == "query_order"
    assert result.data.refundable_amount == Decimal("50.00")

    rows = await audit_rows(session_factory, ticket_id)
    assert [row.outcome for row in rows] == ["succeeded"]


async def test_a_failing_adapter_is_audited_without_leaking_its_message(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_demo_data: None,
    chroma_path: Path,
) -> None:
    ticket_id, trace_id = await seed_ticket(session_factory)

    async with build_executor(session_factory, chroma_path, ticket_id, trace_id) as executor:
        result = await executor.execute(
            agent_id="action",
            tool_name="check_service",
            raw_args={"service_name": "not-a-real-service"},
        )

    assert result.outcome is ToolOutcome.FAILED_FINAL
    assert result.error_code == "TOOL_EXECUTION_FAILED"

    rows = await audit_rows(session_factory, ticket_id)
    assert [row.outcome for row in rows] == ["failed_final"]
    assert "not-a-real-service" not in str(rows[0].result_summary)
