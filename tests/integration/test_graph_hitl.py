"""Integration tests for LangGraph Human-in-the-loop (HITL) interrupt and resume."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import (
    ActionProposal,
    FinalResponse,
    Intent,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.config import AppSettings
from backend.db.models import OrderRecord, RefundRecord, TicketRecord
from backend.db.repositories.approvals import ApprovalRepository
from backend.db.repositories.tickets import TicketRepository
from backend.graph.builder import build_ticket_graph
from backend.graph.checkpointing import create_postgres_checkpointer
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.integration.conftest import APPROVER_ID, REQUESTER_ID, to_psycopg_url
from tests.support.fakes.model_client import FakeModelClient
from tools.schemas import ProcessRefundArgs


async def _create_test_order(
    session: AsyncSession, order_id: str, amount: Decimal = Decimal("500.00")
) -> None:
    session.add(
        OrderRecord(
            id=order_id,
            requester_id=REQUESTER_ID,
            refundable_amount=amount,
            currency="USD",
            status="paid",
            version=1,
        )
    )
    await session.commit()


@pytest.fixture
async def checkpointer_pool(migrated_database: str) -> AsyncConnectionPool:
    psycopg_url = to_psycopg_url(migrated_database)
    pool = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool.open()
    checkpointer = create_postgres_checkpointer(pool)
    await checkpointer.setup()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def checkpointer(checkpointer_pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    return create_postgres_checkpointer(checkpointer_pool)


async def test_high_risk_refund_interrupts_at_waiting_approval(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    order_id = f"ord-hitl-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        await _create_test_order(session, order_id, Decimal("500.00"))
        t = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id=REQUESTER_ID,
            user_query=f"I want a refund for order {order_id} of 250 USD",
            trace_id=uuid.uuid4(),
            status="queued",
            step_count=0,
        )
        session.add(t)
        await session.commit()

    prompts = load_all_prompts()
    settings = AppSettings(
        database_url=migrated_database,
        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",
        openai_model="test-model",
    )

    # Fake model: Supervisor -> action, Action -> refund proposal (250 USD, High Risk)
    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.HIGH_RISK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="process_refund",
                arguments=ProcessRefundArgs(
                    requester_id=REQUESTER_ID,
                    order_id=order_id,
                    amount=Decimal("250.00"),
                    currency="USD",
                    order_version=1,
                ),
                evidence_result_ids=[],
            ),
            RoutingDecision(
                intent=Intent.HIGH_RISK,
                next_agent="finalize",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="The refund completed successfully.",
            ),
        ]
    )

    context = GraphContext(
        model_client=model,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(context, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    # Execute graph until interrupt
    status = await runner.run(str(ticket_id))
    assert status == TicketStatus.WAITING_APPROVAL

    # Verify DB ticket status
    async with session_factory() as session:
        t_rec = await TicketRepository(session).get(ticket_id)
        assert t_rec is not None
        assert t_rec.status == "waiting_approval"

        # Verify ApprovalRecord exists in DB
        app_repo = ApprovalRepository(session)
        apps = await app_repo.list_for_ticket(ticket_id)
        assert len(apps) == 1
        app = apps[0]
        assert app.status == "pending"
        assert app.tool_name == "process_refund"
        assert app.risk_level == "high"

        # Verify side effects strictly 0
        refunds_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert refunds_count == 0

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == Decimal("500.00")
        assert order.version == 1


async def test_approve_resume_executes_write_tool_and_resolves(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    order_id = f"ord-hitl-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        await _create_test_order(session, order_id, Decimal("500.00"))
        t = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id=REQUESTER_ID,
            user_query=f"I want a refund for order {order_id} of 250 USD",
            trace_id=uuid.uuid4(),
            status="queued",
            step_count=0,
        )
        session.add(t)
        await session.commit()

    prompts = load_all_prompts()
    settings = AppSettings(
        database_url=migrated_database,
        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",
        openai_model="test-model",
    )

    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.HIGH_RISK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
                ActionProposal(
                tool_name="process_refund",
                arguments=ProcessRefundArgs(
                    requester_id=REQUESTER_ID,
                    order_id=order_id,
                    amount=Decimal("250.00"),
                    currency="USD",
                    order_version=1,
                ),
                    evidence_result_ids=[],
                ),
                RoutingDecision(
                    intent=Intent.HIGH_RISK,
                    next_agent="finalize",
                    confidence=0.95,
                    escalate=False,
                    reason_code=RoutingReason.NEEDS_KNOWLEDGE,
                ),
                FinalResponse(
                    resolution_mode=ResolutionMode.AUTOMATED,
                    final_response="The refund completed successfully.",
                ),
        ]
    )

    context = GraphContext(
        model_client=model,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(context, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    # 1. Run to interrupt
    status = await runner.run(str(ticket_id))
    assert status == TicketStatus.WAITING_APPROVAL

    # 2. Get approval record and decide 'approve'
    async with session_factory() as session:
        app_repo = ApprovalRepository(session)
        apps = await app_repo.list_for_ticket(ticket_id)
        assert len(apps) == 1
        approval_id = apps[0].id
        decided = await app_repo.decide(
            approval_id,
            expected_version=1,
            decision="approve",
            approver_id=APPROVER_ID,
            reason="Approved by manager",
        )
        await session.commit()
        assert decided.status == "approved"

    # 3. Resume graph
    resume_status = await runner.resume(str(ticket_id), str(approval_id))
    assert resume_status == TicketStatus.RESOLVED

    # 4. Verify DB state: ticket is resolved, 1 refund record, order updated
    async with session_factory() as session:
        t_rec = await TicketRepository(session).get(ticket_id)
        assert t_rec is not None
        assert t_rec.status == "resolved"

        refunds = (
            await session.execute(
                select(RefundRecord).where(RefundRecord.ticket_id == ticket_id)
            )
        ).scalars().all()
        assert len(refunds) == 1
        assert refunds[0].amount == Decimal("250.00")

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == Decimal("250.00")
        assert order.version == 2


async def test_reject_resume_transitions_to_rejected_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    order_id = f"ord-hitl-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        await _create_test_order(session, order_id, Decimal("500.00"))
        t = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id=REQUESTER_ID,
            user_query=f"I want a refund for order {order_id} of 300 USD",
            trace_id=uuid.uuid4(),
            status="queued",
            step_count=0,
        )
        session.add(t)
        await session.commit()

    prompts = load_all_prompts()
    settings = AppSettings(
        database_url=migrated_database,
        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",
        openai_model="test-model",
    )

    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.HIGH_RISK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
                ActionProposal(
                tool_name="process_refund",
                arguments=ProcessRefundArgs(
                    requester_id=REQUESTER_ID,
                    order_id=order_id,
                    amount=Decimal("300.00"),
                    currency="USD",
                    order_version=1,
                ),
                    evidence_result_ids=[],
                ),
                RoutingDecision(
                    intent=Intent.HIGH_RISK,
                    next_agent="finalize",
                    confidence=0.95,
                    escalate=False,
                    reason_code=RoutingReason.NEEDS_KNOWLEDGE,
                ),
                FinalResponse(
                    resolution_mode=ResolutionMode.AUTOMATED,
                    final_response="The refund completed successfully.",
                ),
        ]
    )

    context = GraphContext(
        model_client=model,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(context, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    # 1. Run to interrupt
    status = await runner.run(str(ticket_id))
    assert status == TicketStatus.WAITING_APPROVAL

    # 2. Get approval record and decide 'reject'
    async with session_factory() as session:
        app_repo = ApprovalRepository(session)
        apps = await app_repo.list_for_ticket(ticket_id)
        assert len(apps) == 1
        approval_id = apps[0].id
        decided = await app_repo.decide(
            approval_id,
            expected_version=1,
            decision="reject",
            approver_id=APPROVER_ID,
            reason="Rejected by manager",
        )
        await session.commit()
        assert decided.status == "rejected"

    # 3. Resume graph
    resume_status = await runner.resume(str(ticket_id), str(approval_id))
    assert resume_status == TicketStatus.REJECTED

    # 4. Verify DB: ticket is rejected, 0 refunds, order untouched
    async with session_factory() as session:
        t_rec = await TicketRepository(session).get(ticket_id)
        assert t_rec is not None
        assert t_rec.status == "rejected"

        refunds_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert refunds_count == 0

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == Decimal("500.00")
        assert order.version == 1
