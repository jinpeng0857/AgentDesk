"""Integration tests for SqlTicketService and SqlAccessRequestService against PostgreSQL."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.action import create_pending_operation
from agents.schemas import ActionProposal, ActorContext, ToolOutcome
from backend.db.models import (
    AccessRequestRecord,
    DownstreamTicketRecord,
    TicketRecord,
)
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.executor import (
    ERROR_POLICY_DENIED,
    ToolAdapters,
    WriteToolExecutor,
)
from backend.integrations.tool_adapters import (
    DisabledCredentialService,
    DisabledRefundService,
    SqlAccessRequestService,
    SqlOrderRepository,
    SqlServiceRepository,
    SqlTicketService,
    SqlUserRepository,
)
from tests.fakes.tools import RecordingKnowledgeStore, knowledge_result
from tools.schemas import (
    CreateTicketArgs,
    CreateTicketResult,
    RequestAccessArgs,
    WriteResultStatus,
)

AUTH_ACTOR = ActorContext(
    user_id="user-requester-001",
    role="requester",
    authenticated=True,
)

OTHER_ACTOR = ActorContext(
    user_id="user-other-999",
    role="requester",
    authenticated=True,
)


@pytest.fixture
async def seeded_ticket(
    session_factory: async_sessionmaker[AsyncSession], seeded_users: None
) -> uuid.UUID:
    ticket_id = uuid.uuid4()
    async with session_factory() as session:
        ticket = TicketRecord(
            id=ticket_id,
            thread_id=uuid.uuid4().hex,
            requester_id="user-requester-001",
            user_query="Need technical investigation",
            trace_id=uuid.uuid4(),
            status="running",
        )
        session.add(ticket)
        await session.commit()
    return ticket_id


async def test_sql_ticket_service_creates_downstream_ticket_in_db(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    args = CreateTicketArgs(
        requester_id="user-requester-001",
        title="Database failover check",
        description="Check database replicas and latency spike",
        source_ticket_id=str(seeded_ticket),
    )

    async with session_factory() as session:
        service = SqlTicketService(session, ticket_id=seeded_ticket)
        result = await service.create_ticket(args)
        await session.commit()

    assert result.status is WriteResultStatus.CREATED
    assert result.downstream_ticket_id.startswith("dt-")

    # Verify persisted in PostgreSQL
    async with session_factory() as session:
        record = await session.get(DownstreamTicketRecord, result.downstream_ticket_id)
        assert record is not None
        assert record.source_ticket_id == seeded_ticket
        assert record.requester_id == "user-requester-001"
        assert record.title == "Database failover check"
        assert record.status == "created"


async def test_sql_access_request_service_creates_access_request_in_db(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    args = RequestAccessArgs(
        requester_id="user-requester-001",
        resource="k8s-production-cluster",
        justification="Debug critical incident in cluster",
    )

    async with session_factory() as session:
        service = SqlAccessRequestService(session, ticket_id=seeded_ticket)
        result = await service.request_access(args)
        await session.commit()

    assert result.status is WriteResultStatus.CREATED
    assert result.access_request_id.startswith("ar-")

    # Verify persisted in PostgreSQL and never directly grants access
    async with session_factory() as session:
        record = await session.get(AccessRequestRecord, result.access_request_id)
        assert record is not None
        assert record.ticket_id == seeded_ticket
        assert record.requester_id == "user-requester-001"
        assert record.resource == "k8s-production-cluster"
        assert record.status == "created"


async def test_write_executor_ticket_creation_idempotency_and_trace_correlation(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    trace_id = uuid.uuid4()
    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="Follow-up investigation",
            description="Detailed investigation required",
            source_ticket_id=str(seeded_ticket),
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    # 1. First execution through gateway
    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=DisabledRefundService(),
            ticket=SqlTicketService(session, ticket_id=seeded_ticket),
            access_request=SqlAccessRequestService(session, ticket_id=seeded_ticket),
        )
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )
        res1 = await executor.execute(
            agent_id="action",
            operation=pending,
            actor=AUTH_ACTOR,
        )
        await session.commit()

    assert res1.outcome is ToolOutcome.SUCCEEDED
    assert isinstance(res1.data, CreateTicketResult)
    downstream_id = res1.data.downstream_ticket_id

    # 2. Second execution with same pending operation -> CACHE_HIT
    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=DisabledRefundService(),
            ticket=SqlTicketService(session, ticket_id=seeded_ticket),
            access_request=SqlAccessRequestService(session, ticket_id=seeded_ticket),
        )
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )
        res2 = await executor.execute(
            agent_id="action",
            operation=pending,
            actor=AUTH_ACTOR,
        )
        await session.commit()

    assert res2.outcome is ToolOutcome.CACHE_HIT
    assert isinstance(res2.data, CreateTicketResult)
    assert res2.data.downstream_ticket_id == downstream_id

    # Verify downstream_tickets row count in DB is strictly 1 (no duplicate creation!)
    async with session_factory() as session:
        stmt = select(func.count()).select_from(DownstreamTicketRecord).where(
            DownstreamTicketRecord.source_ticket_id == seeded_ticket
        )
        count = (await session.execute(stmt)).scalar_one()
        assert count == 1

        # Check operation ledger
        op = await OperationRepository(session).get_by_operation_id(pending.operation_id)
        assert op is not None
        assert op.status == "succeeded"
        assert op.ticket_id == seeded_ticket

        # Check audit records correlated
        audit_records = await AuditRepository(session).list_for_ticket(seeded_ticket)
        assert len(audit_records) == 2
        # Both attempts share ticket_id, trace_id, and operation_id
        for record in audit_records:
            assert record.ticket_id == seeded_ticket
            assert record.trace_id == trace_id
            assert record.operation_id == pending.operation_id


async def test_write_executor_cross_user_access_request_leaves_zero_db_rows(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    trace_id = uuid.uuid4()
    proposal = ActionProposal(
        tool_name="request_access",
        arguments=RequestAccessArgs(
            requester_id="user-requester-001",
            resource="restricted-db",
            justification="Unauthorized cross-user attempt",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=DisabledRefundService(),
            ticket=SqlTicketService(session, ticket_id=seeded_ticket),
            access_request=SqlAccessRequestService(session, ticket_id=seeded_ticket),
        )
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )
        result = await executor.execute(
            agent_id="action",
            operation=pending,
            actor=OTHER_ACTOR,  # Cross-user!
        )
        await session.commit()

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_POLICY_DENIED

    # Verify zero rows added to access_requests table
    async with session_factory() as session:
        stmt = select(func.count()).select_from(AccessRequestRecord).where(
            AccessRequestRecord.ticket_id == seeded_ticket
        )
        count = (await session.execute(stmt)).scalar_one()
        assert count == 0
