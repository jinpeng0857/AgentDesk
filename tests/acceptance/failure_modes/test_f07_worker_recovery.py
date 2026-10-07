"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from backend.config import AppSettings
from backend.db.models import (
    OrderRecord,
    RefundRecord,
    TicketRecord,
    ToolCallRecord,
    ToolOperationRecord,
)
from backend.governance.executor import (
    ToolAdapters,
    WriteToolExecutor,
)
from backend.graph.builder import build_ticket_graph
from backend.graph.checkpointing import create_postgres_checkpointer
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.integration.conftest import REQUESTER_ID, to_psycopg_url
from tests.support.fakes.model_client import FakeModelClient
from tests.support.fakes.tools import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledTicketService,
    InMemoryAuditSink,
    RecordingKnowledgeStore,
    RecordingServiceRepository,
    RecordingUserRepository,
    knowledge_result,
    service_result,
    user_result,
)
from tools.registry import (
    TOOL_REGISTRY,
)
from tools.schemas import (
    ProcessRefundArgs,
    ProcessRefundResult,
)


class SimulatedCrashError(RuntimeError):

    """Simulated worker process crash."""


async def test_f07_worker_crash_after_ledger_commit_recovers_without_duplicate_writes(

    session_factory: async_sessionmaker[AsyncSession],

    migrated_database: str,

    seeded_users: None,

    monkeypatch: pytest.MonkeyPatch,

) -> None:

    """F-07: Crash after write commit but before checkpoint, then recover by ledger.



    Ensures:

    1. Worker 1 crashes before completing the graph.

    2. Operation ledger holds the committed completed operation.

    3. Worker 2 resumes from PostgreSQL checkpoint.

    4. Tool ledger hits cache without re-invoking the side-effect.

    5. Final business records (refunds & balance deductions) executed strictly once.

    """

    ticket_id = uuid.uuid4()

    trace_id = uuid.uuid4()

    order_id = f"ORD-F07-{uuid.uuid4().hex[:6].upper()}"

    initial_amount = Decimal("100.00")

    refund_amount = Decimal("50.00")

    psycopg_url = to_psycopg_url(migrated_database)



    # 1. Seed Order and Queued Ticket

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

                user_query=f"Please refund order {order_id} $50.00",

                trace_id=trace_id,

                status="queued",

                step_count=0,

            )

        )

        await session.commit()



    prompts = load_all_prompts()

    settings = AppSettings(

        database_url=migrated_database,

        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",

        openai_model="test-model",

    )



    # 2. Worker 1 Execution & Crash

    pool_1 = AsyncConnectionPool(

        conninfo=psycopg_url,

        max_size=5,

        kwargs={"autocommit": True, "prepare_threshold": 0},

        open=False,

    )

    await pool_1.open()

    checkpointer_1 = create_postgres_checkpointer(pool_1)

    try:

        await checkpointer_1.setup()

    except BaseException:

        await pool_1.close()

        raise

    audit_sink_1 = InMemoryAuditSink()

    adapters_1 = ToolAdapters(

        knowledge=RecordingKnowledgeStore(result=knowledge_result()),

        user=RecordingUserRepository(result=user_result()),

        order=None,  # Use real DB order repository

        service=RecordingServiceRepository(result=service_result()),

        credential=DisabledCredentialService(),

        refund=None,  # Use real DB refund repository

        ticket=DisabledTicketService(),

        access_request=DisabledAccessRequestService(),

    )

    tool_executor_1 = WriteToolExecutor(

        adapters=adapters_1,

        audit=audit_sink_1,

        ticket_id=ticket_id,

        trace_id=trace_id,

        registry=TOOL_REGISTRY,

    )



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

                    requester_id=REQUESTER_ID,

                    order_id=order_id,

                    amount=refund_amount,

                    currency="USD",

                    order_version=1,

                ),

                evidence_result_ids=[],

            ),

        ]

    )



    ctx_1 = GraphContext(

        model_client=model_1,

        prompts=prompts,

        settings=settings,

        session_factory=session_factory,

        tool_executor=tool_executor_1,

    )

    graph_1 = build_ticket_graph(ctx_1, checkpointer=checkpointer_1)

    runner_1 = GraphTicketRunner(

        session_factory=session_factory,

        checkpointer=checkpointer_1,

        graph=graph_1,

    )



    from backend.integrations.tool_adapters import SqlRefundService



    original_refund = SqlRefundService.process_refund



    async def process_refund_then_arm_crash(

        refund_service: SqlRefundService,

        args: ProcessRefundArgs,

    ) -> ProcessRefundResult:

        result = await original_refund(refund_service, args)

        refund_service._session.info["agentdesk_f07_crash_after_commit"] = True

        return result



    original_commit = AsyncSession.commit

    crash_count = 0



    async def commit_then_crash_after_refund(session: AsyncSession) -> None:

        nonlocal crash_count

        crash_after_commit = session.info.pop("agentdesk_f07_crash_after_commit", False)

        await original_commit(session)

        if crash_after_commit:

            crash_count += 1

            raise SimulatedCrashError(

                "simulated worker crash after durable refund commit"

            )



    monkeypatch.setattr(SqlRefundService, "process_refund", process_refund_then_arm_crash)

    monkeypatch.setattr(AsyncSession, "commit", commit_then_crash_after_refund)

    try:

        with pytest.raises(SimulatedCrashError, match="after durable refund commit"):

            await runner_1.run(str(ticket_id))

    finally:

        await pool_1.close()

    assert crash_count == 1



    # 3. Verify Intermediate State: Ledger committed, refund inserted, balance deducted

    async with session_factory() as session:

        ops = (

            await session.scalars(

                select(ToolOperationRecord).where(

                    ToolOperationRecord.ticket_id == ticket_id

                )

            )

        ).all()

        assert len(ops) == 1

        assert ops[0].status == "succeeded"



        refunds_post_crash = (

            await session.scalars(

                select(RefundRecord).where(RefundRecord.ticket_id == ticket_id)

            )

        ).all()

        assert len(refunds_post_crash) == 1



        order_post_crash = await session.get(OrderRecord, order_id)

        assert order_post_crash is not None

        assert order_post_crash.refundable_amount == initial_amount - refund_amount



    # 4. Worker 2: Start new pool, new checkpointer, recover from checkpoint

    pool_2 = AsyncConnectionPool(

        conninfo=psycopg_url,

        max_size=5,

        kwargs={"autocommit": True, "prepare_threshold": 0},

        open=False,

    )

    await pool_2.open()

    checkpointer_2 = create_postgres_checkpointer(pool_2)



    audit_sink_2 = InMemoryAuditSink()

    adapters_2 = ToolAdapters(

        knowledge=RecordingKnowledgeStore(result=knowledge_result()),

        user=RecordingUserRepository(result=user_result()),

        order=None,

        service=RecordingServiceRepository(result=service_result()),

        credential=DisabledCredentialService(),

        refund=None,

        ticket=DisabledTicketService(),

        access_request=DisabledAccessRequestService(),

    )

    tool_executor_2 = WriteToolExecutor(

        adapters=adapters_2,

        audit=audit_sink_2,

        ticket_id=ticket_id,

        trace_id=trace_id,

        registry=TOOL_REGISTRY,

    )



    model_2 = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.QUERY,
                next_agent="finalize",
                confidence=0.98,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="The refund completed successfully.",
            )
        ]
    )



    ctx_2 = GraphContext(

        model_client=model_2,

        prompts=prompts,

        settings=settings,

        session_factory=session_factory,

        tool_executor=tool_executor_2,

    )

    graph_2 = build_ticket_graph(ctx_2, checkpointer=checkpointer_2)

    runner_2 = GraphTicketRunner(

        session_factory=session_factory,

        checkpointer=checkpointer_2,

        graph=graph_2,

    )



    try:

        final_status = await runner_2.run(str(ticket_id))

    finally:

        await pool_2.close()



    assert final_status == TicketStatus.RESOLVED

    assert [call.output_model for call in model_2.calls] == [RoutingDecision, FinalResponse]



    # 5. Final Side-Effects & Projection Verification

    async with session_factory() as session:

        ticket_final = await session.get(TicketRecord, ticket_id)

        assert ticket_final is not None

        assert ticket_final.status == TicketStatus.RESOLVED.value



        final_refunds = (

            await session.scalars(

                select(RefundRecord).where(RefundRecord.ticket_id == ticket_id)

            )

        ).all()

        assert len(final_refunds) == 1, (

            f"Expected strictly 1 refund record, found {len(final_refunds)}!"

        )



        final_order = await session.get(OrderRecord, order_id)

        assert final_order is not None

        assert final_order.refundable_amount == initial_amount - refund_amount, (

            f"Balance was deducted multiple times! Current: {final_order.refundable_amount}"

        )



        refund_audits = (

            await session.scalars(

                select(ToolCallRecord).where(

                    ToolCallRecord.ticket_id == ticket_id,

                    ToolCallRecord.tool_name == "process_refund",

                )

            )

        ).all()

        assert len(refund_audits) == 2

        assert {record.outcome for record in refund_audits} == {

            ToolOutcome.SUCCEEDED.value,

            ToolOutcome.CACHE_HIT.value,

        }

        assert all(record.operation_id for record in refund_audits)

        assert all(record.trace_id == trace_id for record in refund_audits)
