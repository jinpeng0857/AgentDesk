"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
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
from backend.db.models import (
    ApprovalRecord,
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
from tools.schemas import (
    ProcessRefundArgs,
)


async def test_f08_concurrent_approval_decisions_and_duplicate_resumes_idempotency(

    session_factory: async_sessionmaker[AsyncSession],

    checkpointer: AsyncPostgresSaver,

    migrated_database: str,

    seeded_users: None,

) -> None:

    """F-08: Concurrency and replay idempotency without relying on Redis locks.



    1. Concurrent approve/approve requests with expected_version=1:

       - Exactly 1 succeeds (status='approved', version=2).

       - Exactly 1 fails with ApprovalConflictError.

    2. Concurrent approve/reject requests on another approval:

       - Exactly 1 succeeds, the other fails with ApprovalConflictError.

    3. Duplicate/replayed resume invocations:

       - runner.resume executed 3 times on the approved ticket.

       - Returns TicketStatus.RESOLVED on every invocation.

       - Exactly 1 refund record created.

       - Exactly 1 deduction from order refundable_amount.

    """

    ticket_id = uuid.uuid4()

    order_id = f"ord-f08-{uuid.uuid4().hex[:8]}"

    initial_amount = Decimal("500.00")

    refund_amount = Decimal("200.00")



    # 1. Seed order and run ticket through graph to waiting_approval

    async with session_factory() as session:

        session.add(

            OrderRecord(

                id=order_id,

                requester_id=REQUESTER_ID,

                refundable_amount=initial_amount,

                currency="USD",

                status="paid",

                version=1,

            )

        )

        session.add(

            TicketRecord(

                id=ticket_id,

                thread_id=str(ticket_id),

                requester_id=REQUESTER_ID,

                user_query="Request refund of 200.00",

                trace_id=uuid.uuid4(),

                status="queued",

                step_count=0,

            )

        )

        await session.commit()



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

                    amount=refund_amount,

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

    prompts = load_all_prompts()

    settings = AppSettings(

        database_url=migrated_database,

        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",

        openai_model="test-model",

    )

    ctx = GraphContext(

        model_client=model,

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



    status = await runner.run(str(ticket_id))

    assert status == TicketStatus.WAITING_APPROVAL



    # Find created approval record

    async with session_factory() as session:

        approval_rec = (

            await session.scalars(

                select(ApprovalRecord).where(ApprovalRecord.ticket_id == ticket_id)

            )

        ).first()

        assert approval_rec is not None

        assert approval_rec.status == "pending"

        assert approval_rec.version == 1

        approval_id = approval_rec.id



    # 2. Concurrency Test A: Concurrent approve/approve with expected_version=1

    async def try_decide(dec: str) -> ApprovalRecord | Exception:

        try:

            async with session_factory() as s:

                repo = ApprovalRepository(s)

                rec = await repo.decide(

                    approval_id,

                    expected_version=1,

                    decision=dec,  # type: ignore[arg-type]

                    approver_id=APPROVER_ID,

                    reason=f"Concurrent {dec}",

                )

                await s.commit()

                return rec

        except Exception as exc:

            return exc



    res1, res2 = await asyncio.gather(try_decide("approve"), try_decide("approve"))

    successes = [r for r in (res1, res2) if isinstance(r, ApprovalRecord)]

    conflicts = [r for r in (res1, res2) if isinstance(r, ApprovalConflictError)]



    assert len(successes) == 1

    assert len(conflicts) == 1

    assert successes[0].status == "approved"

    assert successes[0].version == 2



    # 3. Concurrency Test B: Concurrent approve/reject on a second approval record

    ticket_id_b = uuid.uuid4()

    async with session_factory() as session:

        session.add(

            TicketRecord(

                id=ticket_id_b,

                thread_id=str(ticket_id_b),

                requester_id=REQUESTER_ID,

                user_query="Second ticket",

                trace_id=uuid.uuid4(),

                status="waiting_approval",

                step_count=3,

            )

        )

        await session.flush()

        repo_b = ApprovalRepository(session)

        appr_b = await repo_b.create_approval(

            ticket_id=ticket_id_b,

            operation_id="op-f08-b",

            tool_name="process_refund",

            risk_level="high",

            args_hash="b" * 64,

            args_summary={"order_id": order_id, "amount": 50.0},

            reason_code="requires_approval",

            expires_at=datetime.now(UTC) + timedelta(hours=24),

        )

        await session.commit()

        appr_b_id = appr_b.id



    async def try_decide_b(dec: str) -> ApprovalRecord | Exception:

        try:

            async with session_factory() as s:

                repo = ApprovalRepository(s)

                rec = await repo.decide(

                    appr_b_id,

                    expected_version=1,

                    decision=dec,  # type: ignore[arg-type]

                    approver_id=APPROVER_ID,

                    reason=f"Concurrent B {dec}",

                )

                await s.commit()

                return rec

        except Exception as exc:

            return exc



    res_b1, res_b2 = await asyncio.gather(try_decide_b("approve"), try_decide_b("reject"))

    successes_b = [r for r in (res_b1, res_b2) if isinstance(r, ApprovalRecord)]

    conflicts_b = [r for r in (res_b1, res_b2) if isinstance(r, ApprovalConflictError)]



    assert len(successes_b) == 1

    assert len(conflicts_b) == 1

    assert successes_b[0].version == 2



    # 4. Repeated resume execution idempotency (3 consecutive / replayed resumes)

    s1 = await runner.resume(str(ticket_id), str(approval_id))

    assert s1 == TicketStatus.RESOLVED



    # Check 1 refund created and balance updated

    async with session_factory() as session:

        refund_count = await session.scalar(

            select(func.count())

            .select_from(RefundRecord)

            .where(RefundRecord.ticket_id == ticket_id)

        )

        assert refund_count == 1



        order = await session.get(OrderRecord, order_id)

        assert order is not None

        assert order.refundable_amount == initial_amount - refund_amount



    # Second resume (replayed job delivery)

    s2 = await runner.resume(str(ticket_id), str(approval_id))

    assert s2 == TicketStatus.RESOLVED



    # Third resume

    s3 = await runner.resume(str(ticket_id), str(approval_id))

    assert s3 == TicketStatus.RESOLVED



    # Verify side effects remain strictly 1: exactly 1 refund record, balance unchanged

    async with session_factory() as session:

        refund_count_final = await session.scalar(

            select(func.count())

            .select_from(RefundRecord)

            .where(RefundRecord.ticket_id == ticket_id)

        )

        assert refund_count_final == 1



        order_final = await session.get(OrderRecord, order_id)

        assert order_final is not None

        assert order_final.refundable_amount == initial_amount - refund_amount
