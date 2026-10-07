"""Deterministic PostgreSQL ledger/checkpoint recovery acceptance drill.

The fixed fault point is immediately after PostgreSQL commits a real refund and
its operation ledger, but before the LangGraph action node returns and checkpoints.
The second worker must replay through the durable ledger without a second refund.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from sqlalchemy import make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

# ruff: noqa: E402

from agents.schemas import (
    ActionProposal,
    FinalResponse,
    Intent,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
    ToolOutcome,
)
from backend.auth.passwords import hash_password
from backend.config import AppSettings
from backend.db.models import (
    OrderRecord,
    RefundRecord,
    TicketRecord,
    ToolCallRecord,
    ToolOperationRecord,
    UserRecord,
)
from backend.graph.builder import build_ticket_graph
from backend.graph.checkpointing import create_postgres_checkpointer
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.integrations.tool_adapters import SqlRefundService
from backend.jobs.tickets import GraphTicketRunner
from tests.support.fakes.model_client import FakeModelClient
from tools.schemas import ProcessRefundArgs, ProcessRefundResult

DRILL_JWT_SECRET = "agentdesk-recovery-drill-secret-is-test-only"


class SimulatedWorkerCrash(RuntimeError):
    """Thrown only after the real business transaction has durably committed."""


def _require_test_database(database_url: str) -> str:
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        raise ValueError("recovery drill refuses databases whose name does not end in '_test'")
    return database_name


def _upgrade_database(database_url: str) -> None:
    environment = {**os.environ, "DATABASE_URL": database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Alembic upgrade failed for the isolated recovery database\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


async def run_recovery_drill(database_url: str) -> None:
    database_name = _require_test_database(database_url)
    _upgrade_database(database_url)
    print("AgentDesk F-07 durable-commit recovery drill")
    print(f"Database: {database_name} (isolated _test target)")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )
    psycopg_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    requester_id = f"user-recovery-{uuid.uuid4().hex[:12]}"
    username = f"recovery-{uuid.uuid4().hex[:12]}"
    order_id = f"ORD-DRILL-{uuid.uuid4().hex[:10].upper()}"
    initial_balance = Decimal("150.00")
    refund_amount = Decimal("50.00")
    pool_1: AsyncConnectionPool | None = None
    pool_2: AsyncConnectionPool | None = None

    try:
        async with session_factory() as session:
            session.add(
                UserRecord(
                    id=requester_id,
                    username=username,
                    password_hash=hash_password(SecretStr("recovery-drill-password")),
                    role="requester",
                    active=True,
                    masked_email="r***@example.invalid",
                    version=1,
                )
            )
            await session.flush()
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
                    user_query=f"Refund order {order_id} for USD 50.00",
                    trace_id=trace_id,
                    status=TicketStatus.QUEUED.value,
                    step_count=0,
                )
            )
            await session.commit()

        settings = AppSettings(
            database_url=database_url,
            jwt_secret=DRILL_JWT_SECRET,
            openai_model="recovery-drill-fake",
        )
        prompts = load_all_prompts()
        pool_1 = AsyncConnectionPool(
            conninfo=psycopg_url,
            max_size=5,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
        await pool_1.open()
        checkpointer_1 = create_postgres_checkpointer(pool_1)
        await checkpointer_1.setup()

        model_1 = FakeModelClient(
            [
                RoutingDecision(
                    intent=Intent.HIGH_RISK,
                    next_agent="action",
                    confidence=0.99,
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
            ]
        )
        graph_1 = build_ticket_graph(
            GraphContext(
                model_client=model_1,
                prompts=prompts,
                settings=settings,
                session_factory=session_factory,
            ),
            checkpointer=checkpointer_1,
        )
        worker_1 = GraphTicketRunner(
            session_factory=session_factory,
            checkpointer=checkpointer_1,
            graph=graph_1,
        )

        original_refund = SqlRefundService.process_refund
        original_commit = AsyncSession.commit
        crash_count = 0

        async def refund_then_arm_crash(
            refund_service: SqlRefundService,
            args: ProcessRefundArgs,
        ) -> ProcessRefundResult:
            result = await original_refund(refund_service, args)
            refund_service._session.info["recovery_drill_crash_after_commit"] = True
            return result

        async def commit_then_crash(session: AsyncSession) -> None:
            nonlocal crash_count
            crash_after_commit = session.info.pop(
                "recovery_drill_crash_after_commit", False
            )
            await original_commit(session)
            if crash_after_commit:
                crash_count += 1
                raise SimulatedWorkerCrash("process stopped after durable refund commit")

        try:
            with patch.object(
                SqlRefundService,
                "process_refund",
                refund_then_arm_crash,
            ), patch.object(AsyncSession, "commit", commit_then_crash):
                try:
                    await worker_1.run(str(ticket_id))
                except SimulatedWorkerCrash as exc:
                    print(f"Fixed crash point reached: {type(exc).__name__}")
                else:
                    raise AssertionError(
                        "worker 1 did not stop at the committed-write fault point"
                    )
        finally:
            await pool_1.close()
            pool_1 = None
        if crash_count != 1:
            raise AssertionError(f"expected one crash after commit, observed {crash_count}")

        async with session_factory() as session:
            operations = list(
                (
                    await session.scalars(
                        select(ToolOperationRecord).where(
                            ToolOperationRecord.ticket_id == ticket_id
                        )
                    )
                ).all()
            )
            refunds = list(
                (
                    await session.scalars(
                        select(RefundRecord).where(RefundRecord.ticket_id == ticket_id)
                    )
                ).all()
            )
            order = await session.get(OrderRecord, order_id)
            if len(operations) != 1 or operations[0].status != "succeeded":
                raise AssertionError("worker 1 did not leave one durable successful ledger row")
            if len(refunds) != 1 or order is None:
                raise AssertionError("worker 1 durable refund row/order is missing")
            if order.refundable_amount != initial_balance - refund_amount:
                raise AssertionError("refund balance did not commit before the crash")

        print("Committed operation, refund row and balance decrement verified once.")

        pool_2 = AsyncConnectionPool(
            conninfo=psycopg_url,
            max_size=5,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=False,
        )
        await pool_2.open()
        checkpointer_2 = create_postgres_checkpointer(pool_2)
        model_2 = FakeModelClient(
            [
                RoutingDecision(
                    intent=Intent.HIGH_RISK,
                    next_agent="finalize",
                    confidence=0.99,
                    escalate=False,
                    reason_code=RoutingReason.COMPLETE,
                ),
                FinalResponse(
                    kind="final",
                    resolution_mode=ResolutionMode.AUTOMATED,
                    final_response="The refund operation is complete.",
                ),
            ]
        )
        graph_2 = build_ticket_graph(
            GraphContext(
                model_client=model_2,
                prompts=prompts,
                settings=settings,
                session_factory=session_factory,
            ),
            checkpointer=checkpointer_2,
        )
        worker_2 = GraphTicketRunner(
            session_factory=session_factory,
            checkpointer=checkpointer_2,
            graph=graph_2,
        )
        try:
            recovered_status = await worker_2.run(str(ticket_id))
        finally:
            await pool_2.close()
            pool_2 = None

        async with session_factory() as session:
            ticket = await session.get(TicketRecord, ticket_id)
            refunds = list(
                (
                    await session.scalars(
                        select(RefundRecord).where(RefundRecord.ticket_id == ticket_id)
                    )
                ).all()
            )
            operations = list(
                (
                    await session.scalars(
                        select(ToolOperationRecord).where(
                            ToolOperationRecord.ticket_id == ticket_id
                        )
                    )
                ).all()
            )
            audits = list(
                (
                    await session.scalars(
                        select(ToolCallRecord).where(
                            ToolCallRecord.ticket_id == ticket_id,
                            ToolCallRecord.tool_name == "process_refund",
                        )
                    )
                ).all()
            )
            order = await session.get(OrderRecord, order_id)

        if recovered_status is not TicketStatus.RESOLVED:
            raise AssertionError(f"recovered ticket ended in {recovered_status!r}")
        if ticket is None or ticket.status != TicketStatus.RESOLVED.value:
            raise AssertionError("terminal ticket projection is missing or not resolved")
        if len(refunds) != 1 or len(operations) != 1 or len(audits) != 2 or order is None:
            raise AssertionError(
                "expected one refund, one operation and two audited gateway attempts "
                f"(observed {len(refunds)}, {len(operations)}, {len(audits)})"
            )
        if order.refundable_amount != initial_balance - refund_amount:
            raise AssertionError("worker recovery repeated the refund balance decrement")
        if {audit.outcome for audit in audits} != {
            ToolOutcome.SUCCEEDED.value,
            ToolOutcome.CACHE_HIT.value,
        }:
            raise AssertionError("recovery did not report one success and one ledger cache hit")
        if any(audit.trace_id != trace_id or audit.operation_id is None for audit in audits):
            raise AssertionError("recovery audit trace/operation binding is incomplete")
        if [call.output_model for call in model_2.calls] != [RoutingDecision, FinalResponse]:
            raise AssertionError("worker 2 did not resume at route/finalize without reproposal")

        print(
            "PASS: recovered resolved ticket; durable refund/ledger once; "
            "success+cache-hit audit; no duplicate side effect."
        )
    finally:
        if pool_1 is not None:
            await pool_1.close()
        if pool_2 is not None:
            await pool_2.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic AgentDesk F-07 recovery drill")
    parser.add_argument(
        "--database-url",
        default=os.getenv("TEST_DATABASE_URL", ""),
        help="PostgreSQL URL for an isolated database whose name ends in _test",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or TEST_DATABASE_URL is required")
    try:
        asyncio.run(run_recovery_drill(args.database_url))
    except Exception as exc:
        print(f"Recovery drill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
