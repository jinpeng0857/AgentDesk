"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import (
    Intent,
    KnowledgeAgentOutput,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.config import AppSettings
from backend.db.models import (
    RefundRecord,
    TicketRecord,
)
from backend.db.repositories.tickets import TicketRepository
from backend.governance.executor import (
    ReadToolExecutor,
    ToolAdapters,
)
from backend.graph.context import GraphContext
from backend.graph.state import TicketState
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


async def test_f03_graph_loop_halts_at_max_steps_and_escalates(

    session_factory: async_sessionmaker[AsyncSession],

    checkpointer: AsyncPostgresSaver,

    migrated_database: str,

    seeded_users: None,

) -> None:

    """F-03: Graph cycle terminates at step 15 without a 16th step.



    Simulates a loop between supervisor and knowledge:

    Supervisor -> Knowledge -> Supervisor -> Knowledge ...

    Must halt at MAX_STEPS = 15, transition to escalated, and never invoke a 16th step.

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

                user_query="Looped routing test query",

                trace_id=trace_id,

                status="queued",

                step_count=0,

            )

        )

        await session.commit()



    # Enqueue 20 alternating valid responses (far more than 15)

    routing_to_knowledge = RoutingDecision(

        intent=Intent.NETWORK,

        next_agent="knowledge",

        confidence=0.95,

        escalate=False,

        reason_code=RoutingReason.NEEDS_KNOWLEDGE,

    )

    knowledge_output = KnowledgeAgentOutput(

        answer="Still investigating network issue.",

        knowledge_refs=[],

        fallback=False,

    )



    looped_responses: list[Any] = []

    for _ in range(15):

        looped_responses.append(routing_to_knowledge)

        looped_responses.append(knowledge_output)



    fake_model = FakeModelClient(looped_responses)



    audit_sink = InMemoryAuditSink()

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

        audit=audit_sink,

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

    from langgraph.graph import END, START, StateGraph

    from backend.graph.edges import route_from_knowledge, route_from_supervisor
    from backend.graph.nodes import (
        make_dispatch_node,
        make_knowledge_node,
        make_supervisor_node,
    )



    workflow = StateGraph(TicketState)

    workflow.add_node("supervisor", make_supervisor_node(ctx))

    workflow.add_node(

        "dispatch_supervisor",

        make_dispatch_node("supervisor", "classify_and_route"),

    )

    workflow.add_node("knowledge", make_knowledge_node(ctx))

    workflow.add_node(

        "dispatch_knowledge",

        make_dispatch_node("knowledge", "ground_knowledge"),

    )

    workflow.add_node(

        "escalate",

        make_dispatch_node("dispatcher", "escalate_ticket", next_status=TicketStatus.ESCALATED),

    )



    workflow.add_edge(START, "supervisor")

    workflow.add_edge("supervisor", "dispatch_supervisor")

    workflow.add_conditional_edges(

        "dispatch_supervisor",

        route_from_supervisor,

        {"knowledge": "knowledge", "escalated": "escalate"},

    )

    workflow.add_edge("knowledge", "dispatch_knowledge")

    workflow.add_conditional_edges(

        "dispatch_knowledge",

        route_from_knowledge,

        {"supervisor": "supervisor", "escalated": "escalate"},

    )

    workflow.add_edge("escalate", END)

    graph = workflow.compile(checkpointer=checkpointer)



    runner = GraphTicketRunner(

        session_factory=session_factory,

        checkpointer=checkpointer,

        graph=graph,

    )



    final_status = await runner.run(str(ticket_id))



    # Assert final status is escalated

    assert final_status == TicketStatus.ESCALATED



    # Verify ticket projection in database: step_count == 15 and status == escalated

    async with session_factory() as session:

        ticket = await TicketRepository(session).get(ticket_id)

        assert ticket is not None

        assert ticket.status == "escalated"

        assert ticket.step_count == 15



        # Check agent history in state: exactly 15 steps

        config = {"configurable": {"thread_id": str(ticket_id)}}

        checkpoint = await checkpointer.aget(config)

        assert checkpoint is not None

        state_history = checkpoint["channel_values"]["agent_history"]

        assert len(state_history) == 15



        # Zero write side-effects

        refund_count = await session.scalar(

            select(func.count())

            .select_from(RefundRecord)

            .where(RefundRecord.ticket_id == ticket_id)

        )

        assert refund_count == 0
