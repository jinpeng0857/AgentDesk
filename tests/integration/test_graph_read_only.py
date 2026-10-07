"""Integration tests for the read-only LangGraph execution pipeline."""

import uuid

import pytest

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
from backend.governance.executor import ReadToolExecutor, ToolAdapters
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.graph.state import initial_ticket_state
from backend.integrations.prompt_loader import load_all_prompts
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


@pytest.mark.asyncio
async def test_read_only_graph_case_a_vpn_resolution() -> None:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    sample_ref = KnowledgeRef(
        document_id="DOC-VPN-001",
        chunk_id="chunk-1",
        title="VPN connectivity troubleshooting",
        content_excerpt="Verify the client version, then check the service status page.",
        score=0.87,
    )

    fake_knowledge_result = knowledge_result()

    knowledge_store = RecordingKnowledgeStore(result=fake_knowledge_result)
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

    fake_model = FakeModelClient(
        [
            # 1. Supervisor routes to Knowledge
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            # 2. Knowledge grounds answer with reference
                KnowledgeAgentOutput(
                    answer="Please follow the VPN restart procedure in the guide.",
                    knowledge_refs=[sample_ref],
                    fallback=False,
                ),
                # 3. Supervisor chooses finalization after reviewing Knowledge output
                RoutingDecision(
                    intent=Intent.NETWORK,
                    next_agent="finalize",
                    confidence=0.95,
                    escalate=False,
                    reason_code=RoutingReason.NEEDS_KNOWLEDGE,
                ),
                # 4. Supervisor finalizes response
                FinalResponse(
                kind="final",
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="Here is the VPN recovery SOP: restart the AnyConnect agent.",
            ),
        ]
    )

    prompts = load_all_prompts()
    settings = AppSettings()
    context = GraphContext(
        model_client=fake_model,
        prompts=prompts,
        settings=settings,
        tool_executor=tool_executor,
    )

    graph = build_ticket_graph(context)

    record = TicketRecord(
        id=ticket_id,
        thread_id=str(ticket_id),
        requester_id="user-requester-001",
        user_query="公司 VPN 无法连接，请查询当前服务状态并给出恢复 SOP。",
        trace_id=trace_id,
        status="running",
        step_count=0,
    )
    initial_state = initial_ticket_state(record)

    final_state = await graph.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": str(ticket_id)}},
    )

    # Assertions per Day 03 Acceptance Criteria:
    assert final_state["status"] is TicketStatus.RESOLVED
    assert final_state["intent"] is Intent.NETWORK
    assert len(final_state["knowledge_refs"]) == 1
    ref = final_state["knowledge_refs"][0]
    assert ref.document_id == "DOC-VPN-001"
    assert ref.chunk_id == "chunk-1"
    assert ref.title == "VPN connectivity troubleshooting"
    assert "Verify the client version" in ref.content_excerpt
    assert ref.score == 0.87

    assert final_state["resolution_mode"] is ResolutionMode.AUTOMATED
    assert "VPN recovery SOP" in str(final_state["final_response"])

    # Step count is strictly bounded
    assert 1 <= final_state["step_count"] <= 15

    # Zero writes occurred
    assert len(cred_service.calls) == 0
    assert len(refund_service.calls) == 0
    assert len(ticket_service.calls) == 0
    assert len(access_service.calls) == 0
