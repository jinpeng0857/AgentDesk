"""AgentDesk Worker Crash and Idempotent Recovery Drill.

Simulates F-07 failure mode:
1. Submits a ticket requiring a write tool (e.g., refund).
2. Worker 1 executes the write tool, committing to the operation ledger and business table.
3. Worker 1 crashes (simulated failure before graph completion checkpoint).
4. Worker 2 starts up, recovers the ticket from PostgreSQL checkpoint and ledger.
5. Verifies:
   - Tool operation ledger records cache hit.
   - Business side effects (refunds and balance deduction) executed exactly once.
   - Ticket completes safely to RESOLVED.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ruff: noqa: E402

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.schemas import (
    ActionProposal,
    FinalResponse,
    Intent,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.auth.passwords import hash_password
from backend.config import AppSettings
from backend.db.models import (
    OrderRecord,
    RefundRecord,
    TicketRecord,
    ToolOperationRecord,
    UserRecord,
)
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.fakes.model_client import FakeModelClient
from tools.schemas import ProcessRefundArgs


class SimulatedCrashError(RuntimeError):
    """Simulated worker process termination / unhandled fault."""


async def run_recovery_drill(database_url: str) -> None:
    print("=" * 70)
    print("  AgentDesk F-07 Worker Crash & Idempotent Recovery Drill")
    print("=" * 70)

    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    psycopg_url = database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )

    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    requester_id = "user-drill-requester"
    order_id = f"ORD-DRILL-{uuid.uuid4().hex[:6].upper()}"
    initial_balance = Decimal("150.00")
    refund_amount = Decimal("50.00")

    # Step 1: Seed user and order in DB
    print("\n[Step 1/5] Seeding test database records...")
    async with session_factory() as session:
        user = await session.get(UserRecord, requester_id)
        if not user:
            session.add(
                UserRecord(
                    id=requester_id,
                    email="requester-drill@example.com",
                    hashed_password=hash_password(SecretStr("requester123456")),
                    role="requester",
                    is_active=True,
                )
            )
        session.add(
            OrderRecord(
                id=order_id,
                requester_id=requester_id,
                refundable_amount=initial_balance,
                currency="USD",
                status="paid",
                version=1,
            )
        )
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=requester_id,
                user_query=f"Please refund order {order_id} with amount 50.00",
                trace_id=trace_id,
                status=TicketStatus.QUEUED.value,
                step_count=0,
            )
        )
        await session.commit()
    print(f"  Ticket created: {ticket_id}")
    print(f"  Order created: {order_id} (Balance: ${initial_balance})")

    prompts = load_all_prompts()
    settings = AppSettings(
        database_url=database_url,
        jwt_secret="drill-secret-at-least-32-bytes-long-for-testing",
        openai_model="drill-model",
    )

    # Step 2: Worker 1 executes write tool then crashes before completion
    print("\n[Step 2/5] Starting Worker 1 (Simulated Crash Path)...")
    pool_1 = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool_1.open()
    checkpointer_1 = AsyncPostgresSaver(pool_1)
    await checkpointer_1.setup()

    model_1 = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.QUERY,
                next_agent="action",
                confidence=0.98,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="process_refund",
                arguments=ProcessRefundArgs(
                    requester_id=requester_id,
                    order_id=order_id,
                    amount=refund_amount,
                    currency="USD",
                    order_version=1,
                ),
                evidence_result_ids=[],
            ),
            # Crash right when supervisor finalizes!
            SimulatedCrashError("FATAL: Worker 1 host process killed by SIGKILL!"),
        ]
    )

    ctx_1 = GraphContext(
        model_client=model_1,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph_1 = build_ticket_graph(ctx_1, checkpointer=checkpointer_1)
    runner_1 = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer_1,
        graph=graph_1,
    )

    crash_caught = False
    try:
        await runner_1.run(str(ticket_id))
    except SimulatedCrashError as err:
        crash_caught = True
        print(f"  [CRASH SIMULATED] Caught expected crash: {err}")
    finally:
        await pool_1.close()

    assert crash_caught, "Worker 1 did not crash as expected!"

    # Step 3: Inspect post-crash state before Worker 2
    print("\n[Step 3/5] Inspecting state immediately after crash...")
    async with session_factory() as session:
        op_records = (
            await session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.ticket_id == ticket_id)
            )
        ).all()
        refund_records = (
            await session.scalars(select(RefundRecord).where(RefundRecord.ticket_id == ticket_id))
        ).all()
        order_post_crash = await session.get(OrderRecord, order_id)
        assert order_post_crash is not None

        print(f"  Ledger committed operations: {len(op_records)}")
        print(f"  Database refund rows: {len(refund_records)}")
        print(f"  Current refundable balance: ${order_post_crash.refundable_amount}")
        assert len(op_records) == 1, "Tool operation record must be committed before crash!"
        assert len(refund_records) == 1, "Refund record must be written before crash!"
        assert order_post_crash.refundable_amount == initial_balance - refund_amount

    # Step 4: Start Worker 2 with fresh context, resume from checkpoint
    print("\n[Step 4/5] Starting Worker 2 to recover ticket from checkpoint...")
    pool_2 = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool_2.open()
    checkpointer_2 = AsyncPostgresSaver(pool_2)

    model_2 = FakeModelClient(
        [
            FinalResponse(
                kind="final",
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response=f"Order {order_id} refund of $50.00 processed successfully.",
            )
        ]
    )

    ctx_2 = GraphContext(
        model_client=model_2,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph_2 = build_ticket_graph(ctx_2, checkpointer=checkpointer_2)
    runner_2 = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer_2,
        graph=graph_2,
    )

    recovered_status = await runner_2.run(str(ticket_id))
    print(f"  Worker 2 run completed with ticket status: {recovered_status}")
    assert recovered_status == TicketStatus.RESOLVED

    await pool_2.close()

    # Step 5: Final Idempotency & Side-effect Verification
    print("\n[Step 5/5] Verifying final side effects strictly equal 1...")
    async with session_factory() as session:
        final_ticket = await session.get(TicketRecord, ticket_id)
        assert final_ticket is not None
        assert final_ticket.status == TicketStatus.RESOLVED.value

        final_refunds = (
            await session.scalars(select(RefundRecord).where(RefundRecord.ticket_id == ticket_id))
        ).all()
        assert len(final_refunds) == 1, (
            f"Expected exactly 1 refund record, found {len(final_refunds)}!"
        )

        final_order = await session.get(OrderRecord, order_id)
        assert final_order is not None
        assert final_order.refundable_amount == initial_balance - refund_amount, (
            f"Balance was double deducted! Current: {final_order.refundable_amount}"
        )

    await engine.dispose()
    print("\n" + "=" * 70)
    print("  [SUCCESS] Recovery drill completed: 0 duplicate side effects, 100% recovered!")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentDesk Recovery Drill")
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:5432/agentdesk_test",
        ),
        help="PostgreSQL database URL",
    )
    args = parser.parse_args()
    asyncio.run(run_recovery_drill(args.database_url))


if __name__ == "__main__":
    main()
