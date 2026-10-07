"""Integration tests for resume idempotency and protection against replay attacks."""

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


async def test_repeated_resume_calls_execute_write_tool_strictly_once(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    order_id = f"ord-idemp-{uuid.uuid4().hex[:8]}"

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

    # 2. Approve
    async with session_factory() as session:
        app_repo = ApprovalRepository(session)
        apps = await app_repo.list_for_ticket(ticket_id)
        approval_id = apps[0].id
        await app_repo.decide(
            approval_id,
            expected_version=1,
            decision="approve",
            approver_id=APPROVER_ID,
            reason="Approved",
        )
        await session.commit()

    # 3. First resume
    s1 = await runner.resume(str(ticket_id), str(approval_id))
    assert s1 == TicketStatus.RESOLVED

    async with session_factory() as session:
        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert refund_count == 1
        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == Decimal("250.00")

    # 4. Second resume (repeat delivery / replay)
    s2 = await runner.resume(str(ticket_id), str(approval_id))
    assert s2 == TicketStatus.RESOLVED

    # 5. Third resume
    s3 = await runner.resume(str(ticket_id), str(approval_id))
    assert s3 == TicketStatus.RESOLVED

    # Verify side effects remain strictly 1
    async with session_factory() as session:
        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert refund_count == 1

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == Decimal("250.00")
        assert order.version == 2
