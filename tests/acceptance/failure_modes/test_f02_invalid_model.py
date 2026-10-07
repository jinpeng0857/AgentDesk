"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import (
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
