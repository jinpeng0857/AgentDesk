"""Integration tests for the PostgreSQL operation ledger and write executor concurrency."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.action import create_pending_operation
from agents.schemas import (
    ActionProposal,
    ActorContext,
    ApprovalSnapshot,
    ApprovalStatus,
    ToolOutcome,
)
from backend.db.models import TicketRecord
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.executor import ToolAdapters, WriteToolExecutor
from backend.governance.idempotency import derive_idempotency_key
from tests.fakes.tools import (
    RecordingKnowledgeStore,
    RecordingOrderRepository,
    RecordingServiceRepository,
    RecordingUserRepository,
    knowledge_result,
    order_result,
    service_result,
    user_result,
)
from tests.unit.test_write_executor import (
    RecordingAccessRequestService,
    RecordingCredentialService,
    RecordingRefundService,
    RecordingTicketService,
)
from tools.schemas import CreateTicketArgs, ProcessRefundArgs

AUTH_ACTOR = ActorContext(
    user_id="user-requester-001",
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
            user_query="Test query",
            trace_id=uuid.uuid4(),
            status="running",
        )
        session.add(ticket)
        await session.commit()
    return ticket_id



async def test_operation_ledger_lease_race(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    operation_id = f"op-{uuid.uuid4().hex[:12]}"
    idempotency_key = derive_idempotency_key("create_ticket", operation_id, "hash-001")

    async with session_factory() as session:
        repo = OperationRepository(session)
        await repo.create_operation(
            ticket_id=seeded_ticket,
            operation_id=operation_id,
            tool_name="create_ticket",
            args_hash="hash-001",
            idempotency_key=idempotency_key,
            status="pending",
        )
        await session.commit()

    # Two concurrent lease attempts
    async def try_acquire(holder: str) -> bool:
        async with session_factory() as session:
            repo = OperationRepository(session)
            acquired = await repo.acquire_lease(
                idempotency_key,
                lease_holder=holder,
                duration_seconds=60,
            )
            await session.commit()
            return acquired

    res1, res2 = await asyncio.gather(
        try_acquire("worker-1"),
        try_acquire("worker-2"),
    )

    # Exactly one worker wins the lease
    assert (res1 and not res2) or (res2 and not res1)

    async with session_factory() as session:
        repo = OperationRepository(session)
        record = await repo.get_by_idempotency_key(idempotency_key)
        assert record is not None
        assert record.status == "running"
        assert record.lease_holder in ("worker-1", "worker-2")


async def test_write_executor_persists_to_postgresql_and_cache_hits(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    trace_id = uuid.uuid4()
    tickets_service = RecordingTicketService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=RecordingCredentialService(),
        refund=RecordingRefundService(),
        ticket=tickets_service,
        access_request=RecordingAccessRequestService(),
    )

    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="Database issue",
            description="Investigate PostgreSQL latency",
            source_ticket_id="src-100",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    # First execution: executes side-effect and writes to PostgreSQL
    async with session_factory() as session:
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )
        res_1 = await executor.execute(
            agent_id="action",
            operation=pending,
            actor=AUTH_ACTOR,
        )
        await session.commit()

    assert res_1.outcome is ToolOutcome.SUCCEEDED
    assert len(tickets_service.calls) == 1

    # Verify persisted in PostgreSQL
    async with session_factory() as session:
        op_repo = OperationRepository(session)
        idempotency_key = derive_idempotency_key(
            pending.tool_name, pending.operation_id, pending.args_hash
        )
        record = await op_repo.get_by_idempotency_key(idempotency_key)
        assert record is not None
        assert record.status == "succeeded"
        assert record.encrypted_result is not None
        assert record.result_summary is not None

    # Second execution: cache hit from PostgreSQL, zero new side effects
    async with session_factory() as session:
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )
        res_2 = await executor.execute(
            agent_id="action",
            operation=pending,
            actor=AUTH_ACTOR,
        )
        await session.commit()

    assert res_2.outcome is ToolOutcome.CACHE_HIT
    # Service calls remained 1!
    assert len(tickets_service.calls) == 1
    assert res_2.data is not None


async def test_concurrent_write_executor_single_side_effect(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    trace_id = uuid.uuid4()
    refund_service = RecordingRefundService(delay_s=0.05)
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=RecordingCredentialService(),
        refund=refund_service,
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )

    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-1",
            amount=Decimal("200.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    approval = ApprovalSnapshot(
        approval_id="app-100",
        operation_id=pending.operation_id,
        args_hash=pending.args_hash,
        status=ApprovalStatus.APPROVED,
        version=1,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    async def execute_once(worker_idx: int) -> ToolOutcome:
        async with session_factory() as session:
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
                actor=AUTH_ACTOR,
                approval=approval,
            )
            await session.commit()
            return result.outcome

    # Launch two concurrent executions with same idempotency key
    outcome1, outcome2 = await asyncio.gather(
        execute_once(1),
        execute_once(2),
        return_exceptions=False,
    )

    # At most one side effect occurred
    assert len(refund_service.calls) == 1
    # One succeeded, other was refused (concurrent) or cache hit
    outcomes = {outcome1, outcome2}
    assert ToolOutcome.SUCCEEDED in outcomes
