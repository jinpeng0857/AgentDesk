"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import (
    ActionProposal,
    FinalResponse,
    Intent,
    KnowledgeAgentOutput,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.config import AppSettings
from backend.db.models import (
    OrderRecord,
    RefundRecord,
    TicketRecord,
    ToolCallRecord,
    ToolOperationRecord,
)
from backend.db.repositories.tickets import TicketRepository
from backend.governance.executor import ReadToolExecutor, ToolAdapters, WriteToolExecutor
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.graph.state import TicketState
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.fakes.failures import FailingKnowledgeStore, FailingRefundService
from tests.fakes.model_client import FakeModelClient
from tests.fakes.tools import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledRefundService,
    DisabledTicketService,
    InMemoryAuditSink,
    RecordingKnowledgeStore,
    RecordingOrderRepository,
    RecordingServiceRepository,
    RecordingUserRepository,
    knowledge_result,
    order_result,
    service_result,
    user_result,
)
from tests.integration.conftest import REQUESTER_ID, to_psycopg_url
from tools.schemas import (
    ProcessRefundArgs,
    ProcessRefundResult,
)

pytest_plugins = ["tests.integration.conftest"]


@pytest.fixture
async def checkpointer_pool(migrated_database: str):
    psycopg_url = to_psycopg_url(migrated_database)
    pool = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def checkpointer(checkpointer_pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    return AsyncPostgresSaver(checkpointer_pool)


@pytest.mark.asyncio
async def test_f01a_optional_read_tool_consecutive_timeouts_falls_back_to_conservative_response(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
) -> None:
    """F-01a: Optional read tool continuous timeout.

    After 3 attempts, falls back to conservative response without citations.
    Final status: resolved + fallback.
    Write side effects: 0.
    """
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    async with session_factory() as session:
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query="公司 VPN 无法连接，请查询当前服务状态并给出恢复 SOP。",
                trace_id=trace_id,
                status="queued",
                step_count=0,
            )
        )
        await session.commit()

    failing_knowledge_store = FailingKnowledgeStore(
        result=knowledge_result(),
        fail_count=3,
    )
    audit_sink = InMemoryAuditSink()

    adapters = ToolAdapters(
        knowledge=failing_knowledge_store,
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=DisabledCredentialService(),
        refund=DisabledRefundService(),
        ticket=DisabledTicketService(),
        access_request=DisabledAccessRequestService(),
    )

    tool_executor = ReadToolExecutor(
        adapters=adapters,
        audit=audit_sink,
        ticket_id=ticket_id,
        trace_id=trace_id,
    )

    # Supervisor routes to knowledge -> Knowledge tries search_knowledge (fails 3 times)
    # -> Knowledge outputs fallback -> Supervisor finalizes with fallback
    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            KnowledgeAgentOutput(
                answer=(
                    "Knowledge base is currently unavailable. "
                    "Please follow standard IT emergency SOP."
                ),
                knowledge_refs=[],
                fallback=True,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.FALLBACK,
                final_response="Knowledge base timed out. Please contact IT support hotline.",
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
        tool_executor=tool_executor,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(ctx, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    final_status = await runner.run(str(ticket_id))

    # Assert final status is resolved
    assert final_status == TicketStatus.RESOLVED

    # Assert exactly 3 attempts made by knowledge store
    assert failing_knowledge_store.call_count == 3

    # Assert exactly 3 audit attempts recorded, all timed_out, retry_count = 0, 1, 2
    timeout_audits = [a for a in audit_sink.attempts if a.tool_name == "search_knowledge"]
    assert len(timeout_audits) == 3
    for idx, attempt in enumerate(timeout_audits):
        assert str(attempt.outcome) == "timed_out"
        assert attempt.retry_count == idx

    # Verify ticket projection in database
    async with session_factory() as session:
        ticket = await TicketRepository(session).get(ticket_id)
        assert ticket is not None
        assert ticket.status == "resolved"
        assert ticket.resolution_mode == "fallback"

        # Citations are empty
        refs = await TicketRepository(session).list_knowledge_refs(ticket_id)
        assert len(refs) == 0

        # Zero write tool calls in DB
        write_audits = (
            await session.scalars(
                select(ToolCallRecord).where(
                    ToolCallRecord.ticket_id == ticket_id,
                    ToolCallRecord.tool_name.in_(
                        ["process_refund", "reset_password", "create_ticket", "request_access"]
                    ),
                )
            )
        ).all()
        assert len(write_audits) == 0


@pytest.mark.asyncio
async def test_f01b_critical_write_tool_consecutive_timeouts_escalates(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
) -> None:
    """F-01b: Critical write tool continuous timeout.

    After 3 attempts, escalates.
    Final status: escalated.
    Ledger: status != 'succeeded'.
    Write side effects: 0.
    """
    ticket_id = uuid.uuid4()
    order_id = f"ord-f01b-{uuid.uuid4().hex[:8]}"
    initial_amount = Decimal("100.00")
    refund_amount = Decimal("50.00")

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
                user_query=f"I want a refund for order {order_id} of 50 USD",
                trace_id=uuid.uuid4(),
                status="queued",
                step_count=0,
            )
        )
        await session.commit()

    failing_refund = FailingRefundService(
        result=ProcessRefundResult(
            refund_id=f"ref-{uuid.uuid4().hex[:8]}",
            status="succeeded",
            amount=refund_amount,
            currency="USD",
        ),
        fail_count=3,
    )

    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.QUERY,
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
    # We wire the custom refund service into action_execute via a custom graph factory or adapters
    from langgraph.graph import END, START, StateGraph

    from agents.schemas import ActorContext
    from backend.db.repositories.audit import AuditRepository
    from backend.db.repositories.operations import OperationRepository
    from backend.governance.audit import SqlAuditSink
    from backend.graph.edges import (
        route_from_action,
        route_from_policy,
        route_from_supervisor,
    )
    from backend.graph.nodes import (
        make_action_propose_node,
        make_approval_node,
        make_dispatch_node,
        make_dispatch_policy_node,
        make_policy_node,
        make_supervisor_finalizer_node,
        make_supervisor_node,
    )
    from backend.integrations.tool_adapters import (
        SqlAccessRequestService,
        SqlCredentialService,
        SqlOrderRepository,
        SqlServiceRepository,
        SqlTicketService,
        SqlUserRepository,
    )

    async def custom_action_execute_node(state: TicketState) -> dict[str, Any]:
        pending_op = state.get("pending_operation")
        assert pending_op is not None
        ticket_id = uuid.UUID(state["ticket_id"])
        trace_id = uuid.UUID(state["trace_id"])
        approval = state.get("approval")

        async with session_factory() as session:
            adapters = ToolAdapters(
                knowledge=RecordingKnowledgeStore(result=knowledge_result()),
                user=SqlUserRepository(session),
                order=SqlOrderRepository(session),
                service=SqlServiceRepository(session),
                credential=SqlCredentialService(session),
                refund=failing_refund,
                ticket=SqlTicketService(session, ticket_id=ticket_id),
                access_request=SqlAccessRequestService(session, ticket_id=ticket_id),
            )
            audit_sink = SqlAuditSink(AuditRepository(session))
            op_repo = OperationRepository(session)
            executor = WriteToolExecutor(
                adapters=adapters,
                audit=audit_sink,
                ticket_id=ticket_id,
                trace_id=trace_id,
                operation_repo=op_repo,
            )
            actor = ActorContext(
                user_id=state["requester_id"],
                role="requester",
                authenticated=True,
            )
            tool_result = await executor.execute(
                agent_id="action",
                operation=pending_op,
                actor=actor,
                approval=approval,
            )
            await session.commit()

        results = list(state.get("tool_results", []))
        results.append(tool_result)
        return {"tool_results": results}

    from backend.graph.nodes import make_dispatch_action_execute_node

    workflow = StateGraph(TicketState)
    workflow.add_node("supervisor", make_supervisor_node(ctx))
    workflow.add_node("dispatch_supervisor", make_dispatch_node("supervisor", "classify_and_route"))
    workflow.add_node("action", make_action_propose_node(ctx))
    workflow.add_node("dispatch_action", make_dispatch_node("action", "propose_operation"))
    workflow.add_node("policy", make_policy_node(ctx))
    workflow.add_node("dispatch_policy", make_dispatch_policy_node())
    workflow.add_node("approval", make_approval_node(ctx))
    workflow.add_node("dispatch_approval", make_dispatch_node("approval", "evaluate_approval"))
    workflow.add_node("action_execute", custom_action_execute_node)
    workflow.add_node("dispatch_action_execute", make_dispatch_action_execute_node())
    workflow.add_node("finalizer", make_supervisor_finalizer_node(ctx))
    workflow.add_node(
        "dispatch_finalizer",
        make_dispatch_node("supervisor", "finalize_response", next_status=TicketStatus.RESOLVED),
    )
    workflow.add_node(
        "escalate",
        make_dispatch_node("dispatcher", "escalate_ticket", next_status=TicketStatus.ESCALATED),
    )
    workflow.add_node(
        "reject",
        make_dispatch_node("approval", "reject_approval", next_status=TicketStatus.REJECTED),
    )

    workflow.add_edge(START, "supervisor")
    workflow.add_edge("supervisor", "dispatch_supervisor")
    workflow.add_conditional_edges(
        "dispatch_supervisor",
        route_from_supervisor,
        {"action": "action", "finalizer": "finalizer", "escalated": "escalate"},
    )
    workflow.add_edge("action", "dispatch_action")
    workflow.add_conditional_edges(
        "dispatch_action",
        route_from_action,
        {"policy": "policy", "finalizer": "finalizer", "escalated": "escalate"},
    )
    workflow.add_edge("policy", "dispatch_policy")
    workflow.add_conditional_edges(
        "dispatch_policy",
        route_from_policy,
        {"approval": "approval", "action_execute": "action_execute", "escalated": "escalate"},
    )
    workflow.add_edge("action_execute", "dispatch_action_execute")
    workflow.add_edge("dispatch_action_execute", END)
    workflow.add_edge("escalate", END)
    workflow.add_edge("reject", END)

    custom_graph = workflow.compile(checkpointer=checkpointer)

    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=custom_graph,
    )

    final_status = await runner.run(str(ticket_id))

    # Assert final status is escalated
    assert final_status == TicketStatus.ESCALATED

    # Assert exactly 3 calls made to refund service
    assert failing_refund.call_count == 3
    # Assert STRICT ZERO business side effects
    assert failing_refund.side_effect_count == 0

    async with session_factory() as session:
        # Check ticket status in database
        ticket = await TicketRepository(session).get(ticket_id)
        assert ticket is not None
        assert ticket.status == "escalated"

        # Check operation ledger: status is failed_final (NOT succeeded)
        op_records = (
            await session.scalars(
                select(ToolOperationRecord).where(ToolOperationRecord.ticket_id == ticket_id)
            )
        ).all()
        assert len(op_records) == 1
        assert op_records[0].status == "failed_final"
        assert op_records[0].result_summary == {"error": "TOOL_TIMEOUT"}

        # Check refund records: strictly 0
        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert refund_count == 0

        # Check order refundable amount is unchanged
        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == initial_amount
        assert order.version == 1

        # Check audits: 3 attempts with timed_out and retry_count 0, 1, 2
        audits = (
            await session.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.ticket_id == ticket_id)
                .order_by(ToolCallRecord.timestamp)
            )
        ).all()
        assert len(audits) == 3
        for idx, audit in enumerate(audits):
            assert audit.outcome == "timed_out"
            assert audit.retry_count == idx
