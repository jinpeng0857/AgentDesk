"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import AppSettings
from backend.db.models import (
    OrderRecord,
    RefundRecord,
    TicketRecord,
)
from backend.db.repositories.approvals import ApprovalConflictError, ApprovalRepository
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.integration.conftest import APPROVER_ID, REQUESTER_ID
from tests.support.fakes.model_client import FakeModelClient


async def test_f05_approval_ttl_expiry_boundaries_and_worker_bypass_prevention(

    session_factory: async_sessionmaker[AsyncSession],

    checkpointer: AsyncPostgresSaver,

    migrated_database: str,

    seeded_users: None,

) -> None:

    """F-05: Approval TTL expiration boundary and worker direct bypass prevention.



    1. Fixed UTC clock injection verifies TTL expiration boundary:

       - Just before expires_at (t - 1s): decide succeeds.

       - Just after expires_at (t + 1s): decide raises ApprovalConflictError (expired).

    2. expire_pending transitions overdue approvals to 'expired'.

    3. Direct worker runner.resume invocation with expired approval raises ApprovalConflictError.

    4. Assertions: 0 refund records, order refundable_amount untouched.

    """

    base_time = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)

    ttl = timedelta(hours=24)

    expires_at = base_time + ttl



    ticket_id_1 = uuid.uuid4()

    ticket_id_2 = uuid.uuid4()

    order_id = f"ord-f05-{uuid.uuid4().hex[:8]}"



    # Seed order and tickets

    async with session_factory() as session:

        session.add(

            OrderRecord(

                id=order_id,

                requester_id=REQUESTER_ID,

                refundable_amount=Decimal("500.00"),

                currency="USD",

                status="paid",

                version=1,

            )

        )

        session.add(

            TicketRecord(

                id=ticket_id_1,

                thread_id=str(ticket_id_1),

                requester_id=REQUESTER_ID,

                user_query="Refund request 1",

                trace_id=uuid.uuid4(),

                status="waiting_approval",

                step_count=3,

            )

        )

        session.add(

            TicketRecord(

                id=ticket_id_2,

                thread_id=str(ticket_id_2),

                requester_id=REQUESTER_ID,

                user_query="Refund request 2",

                trace_id=uuid.uuid4(),

                status="waiting_approval",

                step_count=3,

            )

        )

        await session.commit()



    # 1. Create two approval records with fixed expires_at

    async with session_factory() as session:

        repo = ApprovalRepository(session)

        appr1 = await repo.create_approval(

            ticket_id=ticket_id_1,

            operation_id="op-f05-1",

            tool_name="process_refund",

            risk_level="high",

            args_hash="1" * 64,

            args_summary={"order_id": order_id, "amount": 100.0},

            reason_code="requires_approval",

            expires_at=expires_at,

        )

        appr2 = await repo.create_approval(

            ticket_id=ticket_id_2,

            operation_id="op-f05-2",

            tool_name="process_refund",

            risk_level="high",

            args_hash="2" * 64,

            args_summary={"order_id": order_id, "amount": 200.0},

            reason_code="requires_approval",

            expires_at=expires_at,

        )

        await session.commit()

        appr1_id = appr1.id

        appr2_id = appr2.id



    # 2. Boundary Test A: Just before expiry (expires_at - 1s)

    t_before = expires_at - timedelta(seconds=1)

    async with session_factory() as session:

        repo = ApprovalRepository(session)

        decided1 = await repo.decide(

            appr1_id,

            expected_version=1,

            decision="approve",

            approver_id=APPROVER_ID,

            now=t_before,

        )

        await session.commit()

        assert decided1.status == "approved"

        assert decided1.version == 2



    # 3. Boundary Test B: Just after expiry (expires_at + 1s)

    t_after = expires_at + timedelta(seconds=1)

    async with session_factory() as session:

        repo = ApprovalRepository(session)

        with pytest.raises(ApprovalConflictError, match="has expired"):

            await repo.decide(

                appr2_id,

                expected_version=1,

                decision="approve",

                approver_id=APPROVER_ID,

                now=t_after,

            )



        # Confirm status remains pending in DB

        appr2_rec = await repo.get_by_id(appr2_id)

        assert appr2_rec is not None

        assert appr2_rec.status == "pending"



        # Run expire_pending with cutoff

        expired_records = await repo.expire_pending(cutoff=t_after)

        await session.commit()

        expired_ids = {r.id for r in expired_records}

        assert appr2_id in expired_ids



        appr2_after_expire = await repo.get_by_id(appr2_id)

        assert appr2_after_expire is not None

        assert appr2_after_expire.status == "expired"



    # 4. Worker direct resume bypass attempt:

    # Attacker tries to resume ticket_2 with expired approval

    prompts = load_all_prompts()

    settings = AppSettings(

        database_url=migrated_database,

        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",

        openai_model="test-model",

    )

    ctx = GraphContext(

        model_client=FakeModelClient([]),

        prompts=prompts,

        settings=settings,

        session_factory=session_factory,

    )

    graph = build_ticket_graph(ctx, checkpointer=checkpointer)

    runner = GraphTicketRunner(

        session_factory=session_factory,

        checkpointer=checkpointer,

        graph=graph,

    )



    with pytest.raises(ApprovalConflictError, match="has expired"):

        await runner.resume(ticket_id_2, appr2_id)



    # 5. Side effects assertion: zero refunds inserted, order untouched

    async with session_factory() as session:

        refund_count = await session.scalar(

            select(func.count())

            .select_from(RefundRecord)

            .where(RefundRecord.ticket_id.in_([ticket_id_1, ticket_id_2]))

        )

        assert refund_count == 0



        order = await session.get(OrderRecord, order_id)

        assert order is not None

        assert order.refundable_amount == Decimal("500.00")
