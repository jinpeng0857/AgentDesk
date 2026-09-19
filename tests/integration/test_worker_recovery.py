"""Integration test for Worker exclusive execution and PostgreSQL checkpoint recovery."""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import (
    FinalResponse,
    Intent,
    KnowledgeAgentOutput,
    KnowledgeRef,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.config import AppSettings
from backend.db.models import TicketRecord
from backend.db.repositories.tickets import TicketRepository
from backend.governance.executor import ReadToolExecutor, ToolAdapters
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.integrations.model_client import ModelTransportError
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner, start_job_id
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


@pytest.mark.asyncio
async def test_worker_recovery_from_postgres_checkpoint(
    session_factory: async_sessionmaker[AsyncSession],
    migrated_database: str,
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    psycopg_url = to_psycopg_url(migrated_database)

    # 1. Seed initial ticket in queued state
    ticket_record = TicketRecord(
        id=ticket_id,
        thread_id=str(ticket_id),
        requester_id=REQUESTER_ID,
        user_query="VPN client fails with authentication error 403 on gateway",
        trace_id=trace_id,
        status=TicketStatus.QUEUED.value,
    )
    async with session_factory() as session:
        repo = TicketRepository(session)
        await repo.add(ticket_record)
        await session.commit()

    sample_ref = KnowledgeRef(
        document_id="DOC-VPN-001",
        chunk_id="chunk-1",
        title="VPN connectivity troubleshooting",
        content_excerpt="Check authentication settings and ensure client version 2.4+.",
        score=0.92,
    )

    prompts = load_all_prompts()
    settings = AppSettings(
        database_url=migrated_database,
        jwt_secret="test-secret-at-least-32-bytes-long-for-testing",
        openai_model="test-model",
    )

    # Track adapters and side effects
    knowledge_store = RecordingKnowledgeStore(result=knowledge_result())
    user_repo = RecordingUserRepository(result=user_result())
    order_repo = RecordingOrderRepository(result=order_result())
    service_repo = RecordingServiceRepository(result=service_result())
    cred_service = DisabledCredentialService()
    refund_service = DisabledRefundService()
    ticket_service = DisabledTicketService()
    access_service = DisabledAccessRequestService()

    adapters = ToolAdapters(
        knowledge=knowledge_store,
        user=user_repo,
        order=order_repo,
        service=service_repo,
        credential=cred_service,
        refund=refund_service,
        ticket=ticket_service,
        access_request=access_service,
    )
    audit_sink = InMemoryAuditSink()
    tool_executor = ReadToolExecutor(
        adapters=adapters,
        audit=audit_sink,
        ticket_id=ticket_id,
        trace_id=trace_id,
    )

    # Stage 1: Setup Worker 1 with fixed failure point on first Knowledge model call
    model_1 = FakeModelClient(
        [
            # 1. Supervisor classifies and routes to knowledge
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            # 2. Knowledge model call FAILS (simulated transient infrastructure failure)
            ModelTransportError("simulated transient network failure during knowledge grounding"),
        ]
    )
    context_1 = GraphContext(
        model_client=model_1,
        prompts=prompts,
        settings=settings,
        tool_executor=tool_executor,
    )

    pool_1 = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool_1.open()
    checkpointer_1 = AsyncPostgresSaver(pool_1)

    graph_1 = build_ticket_graph(context_1, checkpointer=checkpointer_1)
    runner_1 = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer_1,
        graph=graph_1,
    )

    # Execute Run 1 - must raise the simulated failure at Knowledge node
    with pytest.raises(ModelTransportError, match="simulated transient network failure"):
        await runner_1.run(str(ticket_id))

    # Assert Stage 1 state:
    # - Model was called twice: 1 supervisor + 1 failed knowledge
    assert len(model_1.calls) == 2
    assert model_1.calls[0].output_model is RoutingDecision

    # - Checkpoint exists in PostgreSQL after Supervisor step
    config = {"configurable": {"thread_id": str(ticket_id)}}
    saved_checkpoint_1 = await checkpointer_1.aget(config)
    assert saved_checkpoint_1 is not None
    assert saved_checkpoint_1["channel_values"]["routing_decision"].next_agent == "knowledge"

    # - Projection in PostgreSQL remains queryable during failure
    async with session_factory() as session:
        repo = TicketRepository(session)
        ticket_state_1 = await repo.get(ticket_id)
        assert ticket_state_1 is not None
        assert ticket_state_1.status == TicketStatus.RUNNING.value
        assert ticket_state_1.intent == Intent.NETWORK.value
        assert ticket_state_1.step_count == 1

    # - Zero write side effects occurred
    assert cred_service.calls == []
    assert refund_service.calls == []
    assert ticket_service.calls == []
    assert access_service.calls == []

    # Close Worker 1 resources (simulate worker crash/reboot)
    await pool_1.close()

    # Stage 2: Create a completely new Worker context, new pool, new checkpointer
    model_2 = FakeModelClient(
        [
            # 1. Knowledge succeeds on retry
            KnowledgeAgentOutput(
                answer="Please follow the VPN restart procedure in the guide DOC-VPN-001.",
                knowledge_refs=[sample_ref],
                fallback=False,
            ),
            # 2. Supervisor finalizer resolves ticket
            FinalResponse(
                kind="final",
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="Please consult DOC-VPN-001 to update your VPN client.",
            ),
        ]
    )
    context_2 = GraphContext(
        model_client=model_2,
        prompts=prompts,
        settings=settings,
        tool_executor=tool_executor,
    )

    pool_2 = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool_2.open()
    checkpointer_2 = AsyncPostgresSaver(pool_2)

    graph_2 = build_ticket_graph(context_2, checkpointer=checkpointer_2)
    runner_2 = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer_2,
        graph=graph_2,
    )

    # Resume execution with the same ticket_id
    final_status = await runner_2.run(str(ticket_id))

    # Assert Stage 2 state:
    # - Ticket reached terminal RESOLVED status
    assert final_status == TicketStatus.RESOLVED

    # - CRITICAL: Supervisor classifier was NOT repeated!
    # model_2 was called only for KnowledgeAgentOutput and FinalResponse
    assert len(model_2.calls) == 2
    assert model_2.calls[0].output_model is KnowledgeAgentOutput
    assert model_2.calls[1].output_model is FinalResponse

    # - Projection in PostgreSQL is fully updated
    async with session_factory() as session:
        repo = TicketRepository(session)
        ticket_state_2 = await repo.get(ticket_id)
        assert ticket_state_2 is not None
        assert ticket_state_2.status == TicketStatus.RESOLVED.value
        assert ticket_state_2.resolution_mode == ResolutionMode.AUTOMATED.value
        assert "DOC-VPN-001" in (ticket_state_2.final_response or "")

        refs = await repo.list_knowledge_refs(ticket_id)
        assert len(refs) == 1
        assert refs[0].document_id == "DOC-VPN-001"
        assert refs[0].chunk_id == "chunk-1"
        assert refs[0].title == "VPN connectivity troubleshooting"

    # - Still zero write side effects
    assert cred_service.calls == []
    assert refund_service.calls == []
    assert ticket_service.calls == []
    assert access_service.calls == []

    # Stage 3: Running again on terminal ticket short-circuits immediately without model calls
    terminal_status = await runner_2.run(str(ticket_id))
    assert terminal_status == TicketStatus.RESOLVED
    assert len(model_2.calls) == 2  # No new calls

    # Deterministic job ID assertion
    assert start_job_id(str(ticket_id)) == f"start:{ticket_id}"

    await pool_2.close()
