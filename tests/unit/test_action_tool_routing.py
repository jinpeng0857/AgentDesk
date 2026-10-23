"""Action read proposals must execute and successful tools must reach finalization."""

from __future__ import annotations

from agents.schemas import (
    ActionProposal,
    Intent,
    PendingOperation,
    RoutingDecision,
    RoutingReason,
    ToolOutcome,
    ToolResult,
)
from backend.db.models import TicketRecord
from backend.graph.edges import route_from_action, route_from_action_execute
from backend.graph.state import initial_ticket_state
from tests.support.fakes.tools import service_result


def test_read_proposal_routes_to_execution_and_success_routes_to_finalizer() -> None:
    record = TicketRecord(
        id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        thread_id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        requester_id="user-001",
        user_query="Is VPN degraded?",
        trace_id="efed6bb0-2f9c-4454-a9f4-511992dc1c86",
        status="running",
        step_count=1,
    )
    state = initial_ticket_state(record)
    state["routing_decision"] = RoutingDecision(
        intent=Intent.NETWORK,
        next_agent="action",
        confidence=0.95,
        escalate=False,
        reason_code=RoutingReason.NEEDS_ACTION,
    )
    state["action_proposal"] = ActionProposal(
        tool_name="check_service",
        arguments={"kind": "check_service", "service_name": "vpn"},
        evidence_result_ids=[],
    )

    assert route_from_action(state) == "action_execute"

    state["tool_results"] = [
        ToolResult(
            request_id="request-001",
            operation_id=None,
            tool_name="check_service",
            outcome=ToolOutcome.SUCCEEDED,
            data=service_result(),
            error_code=None,
            retry_count=0,
            latency_ms=1,
        )
    ]
    assert route_from_action_execute(state) == "supervisor"


def test_failed_action_tool_routes_to_escalation() -> None:
    record = TicketRecord(
        id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        thread_id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        requester_id="user-001",
        user_query="Is VPN degraded?",
        trace_id="efed6bb0-2f9c-4454-a9f4-511992dc1c86",
        status="running",
        step_count=1,
    )
    state = initial_ticket_state(record)
    state["tool_results"] = [
        ToolResult(
            request_id="request-002",
            operation_id=None,
            tool_name="check_service",
            outcome=ToolOutcome.REJECTED,
            data=None,
            error_code="TOOL_NOT_PERMITTED",
            retry_count=0,
            latency_ms=1,
        )
    ]

    assert route_from_action_execute(state) == "escalated"


def test_repeated_successful_read_proposal_routes_to_finalizer_without_execution() -> None:
    record = TicketRecord(
        id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        thread_id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        requester_id="user-001",
        user_query="Check the VPN status",
        trace_id="efed6bb0-2f9c-4454-a9f4-511992dc1c86",
        status="running",
        step_count=4,
    )
    state = initial_ticket_state(record)
    state["action_proposal"] = ActionProposal(
        tool_name="check_service",
        arguments={"kind": "check_service", "service_name": "vpn"},
        evidence_result_ids=[],
    )
    state["tool_results"] = [
        ToolResult(
            request_id="request-001",
            operation_id=None,
            tool_name="check_service",
            outcome=ToolOutcome.SUCCEEDED,
            data=service_result(),
            error_code=None,
            retry_count=0,
            latency_ms=1,
        )
    ]

    assert route_from_action(state) == "finalizer"


def test_repeated_successful_write_proposal_routes_to_finalizer_without_execution() -> None:
    record = TicketRecord(
        id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        thread_id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        requester_id="user-001",
        user_query="Request access to a repository",
        trace_id="efed6bb0-2f9c-4454-a9f4-511992dc1c86",
        status="running",
        step_count=4,
    )
    state = initial_ticket_state(record)
    state["pending_operation"] = PendingOperation(
        operation_id="operation-2",
        tool_name="request_access",
        validated_args={
            "kind": "request_access",
            "requester_id": "user-001",
            "resource": "repo:agentdesk",
            "justification": "Engineering work",
        },
        args_hash="a" * 64,
        side_effects="write",
        evidence_result_ids=[],
        proposed_by="action",
        proposed_at="2026-10-21T00:00:00Z",
        schema_version="1.0.0",
    )
    state["tool_results"] = [
        ToolResult(
            request_id="request-001",
            operation_id="operation-1",
            tool_name="request_access",
            outcome=ToolOutcome.SUCCEEDED,
            data=None,
            error_code=None,
            retry_count=0,
            latency_ms=1,
        )
    ]

    assert route_from_action(state) == "finalizer"
