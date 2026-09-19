"""Integration tests for SqlRefundService and WriteToolExecutor refund execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.action import create_pending_operation
from agents.schemas import (
    ActionProposal,
    ActorContext,
    ApprovalSnapshot,
    ApprovalStatus,
    ResourceContext,
    ToolOutcome,
)
from backend.db.models import OrderRecord, RefundRecord, TicketRecord
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.executor import (
    ERROR_APPROVAL_REQUIRED,
    ToolAdapters,
    WriteToolExecutor,
)
from backend.integrations.tool_adapters import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledTicketService,
    SqlOrderRepository,
    SqlRefundService,
    SqlServiceRepository,
    SqlUserRepository,
)
from tests.fakes.tools import RecordingKnowledgeStore, knowledge_result
from tools.schemas import ProcessRefundArgs, ProcessRefundResult, WriteResultStatus

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
            user_query="Need refund for recent transaction",
            trace_id=uuid.uuid4(),
            status="running",
        )
        session.add(ticket)
        await session.commit()
    return ticket_id


@pytest.fixture
async def seeded_order(
    session_factory: async_sessionmaker[AsyncSession], seeded_users: None
) -> str:
    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        order = OrderRecord(
            id=order_id,
            requester_id="user-requester-001",
            status="paid",
            refundable_amount=Decimal("500.00"),
            currency="USD",
            version=1,
        )
        session.add(order)
        await session.commit()
    return order_id


async def test_sql_refund_service_partial_and_full_refund(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
    seeded_order: str,
) -> None:
    # 1. Partial refund of 50.00 USD
    async with session_factory() as session:
        service = SqlRefundService(session, ticket_id=seeded_ticket)
        res1 = await service.process_refund(
            ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=seeded_order,
                amount=Decimal("50.00"),
                currency="USD",
                order_version=1,
            )
        )
        await session.commit()

    assert res1.status == WriteResultStatus.SUCCEEDED
    assert res1.amount == Decimal("50.00")
    assert res1.refund_id.startswith("ref-")

    # Verify DB state after partial refund
    async with session_factory() as session:
        order = await session.get(OrderRecord, seeded_order)
        assert order is not None
        assert order.refundable_amount == Decimal("450.00")
        assert order.status == "paid"
        assert order.version == 2

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.order_id == seeded_order)
        )
        assert refund_count == 1

    # 2. Full remaining refund of 450.00 USD
    async with session_factory() as session:
        service = SqlRefundService(session, ticket_id=seeded_ticket)
        res2 = await service.process_refund(
            ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=seeded_order,
                amount=Decimal("450.00"),
                currency="USD",
                order_version=2,
            )
        )
        await session.commit()

    assert res2.status == WriteResultStatus.SUCCEEDED
    assert res2.amount == Decimal("450.00")

    # Verify DB state after complete refund
    async with session_factory() as session:
        order = await session.get(OrderRecord, seeded_order)
        assert order is not None
        assert order.refundable_amount == Decimal("0.00")
        assert order.status == "refunded"
        assert order.version == 3

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.order_id == seeded_order)
        )
        assert refund_count == 2


async def test_sql_refund_service_state_validation(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
    seeded_order: str,
) -> None:
    async with session_factory() as session:
        service = SqlRefundService(session, ticket_id=seeded_ticket)

        # 1. Non-existent order
        with pytest.raises(LookupError, match="does not exist"):
            await service.process_refund(
                ProcessRefundArgs(
                    requester_id="user-requester-001",
                    order_id="ord-non-existent",
                    amount=Decimal("10.00"),
                    currency="USD",
                    order_version=1,
                )
            )

        # 2. Cross-user order ownership mismatch
        with pytest.raises(PermissionError, match="belongs to"):
            await service.process_refund(
                ProcessRefundArgs(
                    requester_id="user-other-999",
                    order_id=seeded_order,
                    amount=Decimal("10.00"),
                    currency="USD",
                    order_version=1,
                )
            )

        # 3. Order version mismatch
        with pytest.raises(ValueError, match="version mismatch"):
            await service.process_refund(
                ProcessRefundArgs(
                    requester_id="user-requester-001",
                    order_id=seeded_order,
                    amount=Decimal("10.00"),
                    currency="USD",
                    order_version=99,
                )
            )

        # 4. Amount exceeds refundable amount (500.00 max)
        with pytest.raises(ValueError, match="exceeds refundable amount"):
            await service.process_refund(
                ProcessRefundArgs(
                    requester_id="user-requester-001",
                    order_id=seeded_order,
                    amount=Decimal("600.00"),
                    currency="USD",
                    order_version=1,
                )
            )

        # 5. Non-positive amount rejected by schema
        with pytest.raises(ValidationError):
            ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=seeded_order,
                amount=Decimal("0.00"),
                currency="USD",
                order_version=1,
            )

        # 6. Service defense-in-depth against bypassed non-positive amount
        with pytest.raises(ValueError, match="positive"):
            await service.process_refund(
                ProcessRefundArgs.model_construct(
                    kind="process_refund",
                    requester_id="user-requester-001",
                    order_id=seeded_order,
                    amount=Decimal("0.00"),
                    currency="USD",
                    order_version=1,
                )
            )


async def test_refund_gateway_medium_auto_executes_199_99_usd(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
    seeded_order: str,
) -> None:
    trace_id = uuid.uuid4()
    operation_id = f"op-refund-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=SqlRefundService(session, ticket_id=seeded_ticket),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=SqlAuditSink(AuditRepository(session)),
            operation_repo=OperationRepository(session),
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )

        proposal = ActionProposal(
            tool_name="process_refund",
            arguments=ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=seeded_order,
                amount=Decimal("199.99"),
                currency="USD",
                order_version=1,
            ),
            evidence_result_ids=[],
        )
        op = create_pending_operation(proposal, operation_id=operation_id)
        res = ResourceContext(
            resource_type="order",
            resource_id=seeded_order,
            owner_id="user-requester-001",
            status="paid",
            version=1,
        )

        result = await executor.execute(
            agent_id="action",
            operation=op,
            actor=AUTH_ACTOR,
            resource=res,
        )
        await session.commit()

    assert result.outcome == ToolOutcome.SUCCEEDED
    assert isinstance(result.data, ProcessRefundResult)
    assert result.data.amount == Decimal("199.99")

    # Verify DB: 1 refund record, order updated
    async with session_factory() as session:
        order = await session.get(OrderRecord, seeded_order)
        assert order is not None
        assert order.refundable_amount == Decimal("300.01")
        assert order.version == 2

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.order_id == seeded_order)
        )
        assert refund_count == 1


async def test_refund_gateway_high_blocked_200_00_usd_without_approval(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
    seeded_order: str,
) -> None:
    trace_id = uuid.uuid4()
    operation_id = f"op-refund-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=SqlRefundService(session, ticket_id=seeded_ticket),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=SqlAuditSink(AuditRepository(session)),
            operation_repo=OperationRepository(session),
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )

        proposal = ActionProposal(
            tool_name="process_refund",
            arguments=ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=seeded_order,
                amount=Decimal("200.00"),
                currency="USD",
                order_version=1,
            ),
            evidence_result_ids=[],
        )
        op = create_pending_operation(proposal, operation_id=operation_id)
        res = ResourceContext(
            resource_type="order",
            resource_id=seeded_order,
            owner_id="user-requester-001",
            status="paid",
            version=1,
        )

        result = await executor.execute(
            agent_id="action",
            operation=op,
            actor=AUTH_ACTOR,
            resource=res,
        )
        await session.commit()

    assert result.outcome == ToolOutcome.REJECTED
    assert result.error_code == ERROR_APPROVAL_REQUIRED

    # Side-effect count is strictly 0: no refund record, order unchanged
    async with session_factory() as session:
        order = await session.get(OrderRecord, seeded_order)
        assert order is not None
        assert order.refundable_amount == Decimal("500.00")
        assert order.version == 1

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.order_id == seeded_order)
        )
        assert refund_count == 0


async def test_refund_gateway_high_succeeds_with_valid_approval(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
    seeded_order: str,
) -> None:
    trace_id = uuid.uuid4()
    operation_id = f"op-refund-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=SqlRefundService(session, ticket_id=seeded_ticket),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=SqlAuditSink(AuditRepository(session)),
            operation_repo=OperationRepository(session),
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )

        proposal = ActionProposal(
            tool_name="process_refund",
            arguments=ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=seeded_order,
                amount=Decimal("200.00"),
                currency="USD",
                order_version=1,
            ),
            evidence_result_ids=[],
        )
        op = create_pending_operation(proposal, operation_id=operation_id)
        res = ResourceContext(
            resource_type="order",
            resource_id=seeded_order,
            owner_id="user-requester-001",
            status="paid",
            version=1,
        )

        approval = ApprovalSnapshot(
            approval_id="app-refund-002",
            operation_id=op.operation_id,
            args_hash=op.args_hash,
            status=ApprovalStatus.APPROVED,
            version=1,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        result = await executor.execute(
            agent_id="action",
            operation=op,
            actor=AUTH_ACTOR,
            resource=res,
            approval=approval,
        )
        await session.commit()

    assert result.outcome == ToolOutcome.SUCCEEDED
    assert isinstance(result.data, ProcessRefundResult)
    assert result.data.amount == Decimal("200.00")

    async with session_factory() as session:
        order = await session.get(OrderRecord, seeded_order)
        assert order is not None
        assert order.refundable_amount == Decimal("300.00")
        assert order.version == 2

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.order_id == seeded_order)
        )
        assert refund_count == 1


async def test_refund_gateway_idempotent_replay_and_concurrency(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
    seeded_order: str,
) -> None:
    trace_id = uuid.uuid4()
    operation_id = f"op-refund-idem-{uuid.uuid4().hex[:8]}"

    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id=seeded_order,
            amount=Decimal("150.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    op = create_pending_operation(proposal, operation_id=operation_id)
    res = ResourceContext(
        resource_type="order",
        resource_id=seeded_order,
        owner_id="user-requester-001",
        status="paid",
        version=1,
    )

    # 1. First execution -> SUCCEEDED
    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=SqlRefundService(session, ticket_id=seeded_ticket),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=SqlAuditSink(AuditRepository(session)),
            operation_repo=OperationRepository(session),
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )

        res1 = await executor.execute(
            agent_id="action",
            operation=op,
            actor=AUTH_ACTOR,
            resource=res,
        )
        await session.commit()

    assert res1.outcome == ToolOutcome.SUCCEEDED
    assert isinstance(res1.data, ProcessRefundResult)
    first_refund_id = res1.data.refund_id

    # 2. Second execution with same operation -> CACHE_HIT
    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=DisabledCredentialService(),
            refund=SqlRefundService(session, ticket_id=seeded_ticket),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=SqlAuditSink(AuditRepository(session)),
            operation_repo=OperationRepository(session),
            ticket_id=seeded_ticket,
            trace_id=trace_id,
        )

        res2 = await executor.execute(
            agent_id="action",
            operation=op,
            actor=AUTH_ACTOR,
            resource=res,
        )
        await session.commit()

    assert res2.outcome == ToolOutcome.CACHE_HIT
    assert isinstance(res2.data, ProcessRefundResult)
    assert res2.data.refund_id == first_refund_id

    # Assert exactly 1 refund in DB, order refundable amount reduced only once!
    async with session_factory() as session:
        order = await session.get(OrderRecord, seeded_order)
        assert order is not None
        assert order.refundable_amount == Decimal("350.00")
        assert order.version == 2

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.order_id == seeded_order)
        )
        assert refund_count == 1
