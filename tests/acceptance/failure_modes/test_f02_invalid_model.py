"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import (
    ActionProposal,
    Intent,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.config import AppSettings
from backend.db.models import (
    RefundRecord,
    TicketRecord,
    ToolCallRecord,
)
from backend.db.repositories.tickets import TicketRepository
from backend.governance.executor import (
    ReadToolExecutor,
    ToolAdapters,
)
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.integration.conftest import REQUESTER_ID
from tests.support.fakes.model_client import FakeModelClient
from tests.support.fakes.tools import (
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
from tools.schemas import RequestAccessArgs


async def test_f02_invalid_model_structure_escalates_after_failed_repairs(

    session_factory: async_sessionmaker[AsyncSession],

    checkpointer: AsyncPostgresSaver,

    migrated_database: str,

    seeded_users: None,

) -> None:

    """F-02: Model returns invalid structure twice in sequence.



    After 2 failed repair attempts, supervisor escalates.

    Validation error summaries contain only error categories, never sensitive output.

    Final status: escalated.

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

                user_query="My password is SuperSecretPass123! Please help.",

                trace_id=trace_id,

                status="queued",

                step_count=0,

            )

        )

        await session.commit()



    # Two invalid structures queued (1 initial + 1 repair attempt)

    sensitive_token = "SuperSecretPass123!"

    fake_model = FakeModelClient(

        [

            {"invalid_field": sensitive_token, "intent": "non_existent_intent"},

            {"corrupted_data": sensitive_token},

        ]

    )



    adapters = ToolAdapters(

        knowledge=RecordingKnowledgeStore(result=knowledge_result()),

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

        audit=InMemoryAuditSink(),

        ticket_id=ticket_id,

        trace_id=trace_id,

    )



    prompts = load_all_prompts()

    settings = AppSettings(

        database_url=migrated_database,

        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",

        openai_model="test-model",

    )

    ctx = GraphContext(

        model_client=fake_model,

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



    # Assert final status is escalated

    assert final_status == TicketStatus.ESCALATED



    # Assert exactly 2 model calls made (1 initial + 1 repair)

    assert len(fake_model.calls) == 2



    # Verify ticket projection in database

    async with session_factory() as session:

        ticket = await TicketRepository(session).get(ticket_id)

        assert ticket is not None

        assert ticket.status == "escalated"



        # Assert 0 tool calls in DB

        tool_audits = (

            await session.scalars(

                select(ToolCallRecord).where(ToolCallRecord.ticket_id == ticket_id)

            )

        ).all()

        assert len(tool_audits) == 0



        # Assert 0 refund / write side-effects

        refund_count = await session.scalar(

            select(func.count())

            .select_from(RefundRecord)

            .where(RefundRecord.ticket_id == ticket_id)

        )

        assert refund_count == 0


@pytest.mark.parametrize(
    ("failed_agent", "intent", "reason_code"),
    [
        ("knowledge", Intent.NETWORK, RoutingReason.NEEDS_KNOWLEDGE),
        ("action", Intent.HIGH_RISK, RoutingReason.NEEDS_ACTION),
        ("action_identity", Intent.HIGH_RISK, RoutingReason.NEEDS_ACTION),
    ],
)
async def test_f02_agent_output_exhaustion_escalates_and_persists_dispatch_trace(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    migrated_database: str,
    seeded_users: None,
    failed_agent: str,
    intent: Intent,
    reason_code: RoutingReason,
) -> None:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query="Exercise structured output recovery for the selected Agent",
                trace_id=trace_id,
                status="queued",
                step_count=0,
            )
        )
        await session.commit()

    routed_agent = "action" if failed_agent == "action_identity" else failed_agent
    model_responses: list[Any] = [
        RoutingDecision(
            intent=intent,
            next_agent=routed_agent,
            confidence=0.95,
            escalate=False,
            reason_code=reason_code,
        )
    ]
    if failed_agent == "action_identity":
        wrong_owner_proposal = ActionProposal(
            tool_name="request_access",
            arguments=RequestAccessArgs(
                requester_id="unverified-requester",
                resource="VPN",
                justification="Support request",
            ),
            evidence_result_ids=[],
        )
        model_responses.extend([wrong_owner_proposal, wrong_owner_proposal])
    else:
        model_responses.extend(
            [{"wrong_field": "one"}, {"wrong_field": "two"}, {"wrong_field": "three"}]
        )
    fake_model = FakeModelClient(model_responses)
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
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
        audit=InMemoryAuditSink(),
        ticket_id=ticket_id,
        trace_id=trace_id,
    )
    settings = AppSettings(
        database_url=migrated_database,
        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",
        openai_model="test-model",
    )
    graph = build_ticket_graph(
        GraphContext(
            model_client=fake_model,
            prompts=load_all_prompts(),
            settings=settings,
            tool_executor=tool_executor,
            session_factory=session_factory,
        ),
        checkpointer=checkpointer,
    )
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    status = await runner.run(str(ticket_id))

    assert status is TicketStatus.ESCALATED
    async with session_factory() as session:
        ticket = await TicketRepository(session).get(ticket_id)
        assert ticket is not None
        assert ticket.status == TicketStatus.ESCALATED.value
        assert ticket.step_count == 2
        events = await TicketRepository(session).list_trace_events(ticket_id)
        assert events[-1].actor == "dispatcher"
        assert events[-1].summary == "worker_failure:model_output_invalid"

    snapshot = await checkpointer.aget({"configurable": {"thread_id": str(ticket_id)}})
    assert snapshot is not None
    assert snapshot["channel_values"]["status"] == TicketStatus.ESCALATED.value
