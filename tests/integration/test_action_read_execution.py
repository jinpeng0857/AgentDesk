"""End-to-end graph coverage for Action-selected read-only tools."""

from __future__ import annotations

import uuid

import pytest

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
from tools.schemas import CheckServiceArgs, QueryUserArgs


@pytest.mark.asyncio
async def test_action_read_tool_runs_through_gateway_and_is_summarized() -> None:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    service_repo = RecordingServiceRepository(result=service_result())
    audit = InMemoryAuditSink()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=service_repo,
        credential=DisabledCredentialService(),
        refund=DisabledRefundService(),
        ticket=DisabledTicketService(),
        access_request=DisabledAccessRequestService(),
    )
    tool_executor = ReadToolExecutor(
        adapters=adapters,
        audit=audit,
        ticket_id=ticket_id,
        trace_id=trace_id,
    )
    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="check_service",
                arguments=CheckServiceArgs(service_name="vpn"),
                evidence_result_ids=[],
            ),
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="finalize",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="The VPN service is degraded.",
            ),
        ]
    )
    context = GraphContext(
        model_client=model,
        prompts=load_all_prompts(),
        settings=AppSettings(openai_model="test-model"),
        tool_executor=tool_executor,
    )
    graph = build_ticket_graph(context)
    ticket = TicketRecord(
        id=ticket_id,
        thread_id=str(ticket_id),
        requester_id="user-requester-001",
        user_query="Is the VPN service degraded?",
        trace_id=trace_id,
        status="queued",
        step_count=0,
    )

    final_state = await graph.ainvoke(
        initial_ticket_state(ticket),
        config={"configurable": {"thread_id": str(ticket_id)}},
    )

    assert final_state["status"] is TicketStatus.RESOLVED
    assert final_state["action_proposal"].tool_name == "check_service"
    assert [call.service_name for call in service_repo.calls] == ["vpn"]
    assert len(final_state["tool_results"]) == 1
    assert final_state["tool_results"][0].tool_name == "check_service"
    assert str(final_state["tool_results"][0].outcome) == "succeeded"
    assert audit.outcomes == ["succeeded"]
    assert len(model.calls) == 4


@pytest.mark.asyncio
async def test_action_can_execute_two_read_tools_before_supervisor_finalizes() -> None:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    user_repo = RecordingUserRepository(result=user_result())
    service_repo = RecordingServiceRepository(result=service_result())
    audit = InMemoryAuditSink()
    tool_executor = ReadToolExecutor(
        adapters=ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=user_repo,
            order=RecordingOrderRepository(result=order_result()),
            service=service_repo,
            credential=DisabledCredentialService(),
            refund=DisabledRefundService(),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        ),
        audit=audit,
        ticket_id=ticket_id,
        trace_id=trace_id,
    )
    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.ACCOUNT,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="query_user",
                arguments=QueryUserArgs(user_id="user-requester-001"),
                evidence_result_ids=[],
            ),
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="check_service",
                arguments=CheckServiceArgs(service_name="vpn"),
                evidence_result_ids=[],
            ),
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="finalize",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="The account is active and VPN is degraded.",
            ),
        ]
    )
    graph = build_ticket_graph(
        GraphContext(
            model_client=model,
            prompts=load_all_prompts(),
            settings=AppSettings(openai_model="test-model"),
            tool_executor=tool_executor,
        )
    )
    ticket = TicketRecord(
        id=ticket_id,
        thread_id=str(ticket_id),
        requester_id="user-requester-001",
        user_query="Check my account and VPN status.",
        trace_id=trace_id,
        status="queued",
        step_count=0,
    )

    final_state = await graph.ainvoke(
        initial_ticket_state(ticket),
        config={"configurable": {"thread_id": str(ticket_id)}},
    )

    assert final_state["status"] is TicketStatus.RESOLVED
    assert [result.tool_name for result in final_state["tool_results"]] == [
        "query_user",
        "check_service",
    ]
    assert [call.user_id for call in user_repo.calls] == ["user-requester-001"]
    assert [call.service_name for call in service_repo.calls] == ["vpn"]
    assert audit.outcomes == ["succeeded", "succeeded"]
    assert [call.output_model for call in model.calls] == [
        RoutingDecision,
        ActionProposal,
        RoutingDecision,
        ActionProposal,
        RoutingDecision,
        FinalResponse,
    ]
