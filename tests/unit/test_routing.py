"""Unit tests for deterministic graph routing, dispatch, and state boundaries."""

import uuid

import pytest

from agents.schemas import (
    Intent,
    KnowledgeRef,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.db.models import TicketRecord
from backend.graph.edges import (
    MAX_STEPS,
    advance_dispatch,
    route_from_knowledge,
    route_from_supervisor,
)
from backend.graph.state import initial_ticket_state


def create_sample_ticket() -> TicketRecord:
    return TicketRecord(
        id=uuid.uuid4(),
        thread_id="thread-123",
        requester_id="user-001",
        user_query="VPN connection failed",
        trace_id=uuid.uuid4(),
        status="running",
        step_count=0,
    )


def test_initial_ticket_state_shape() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)

    assert state["ticket_id"] == str(record.id)
    assert state["thread_id"] == "thread-123"
    assert state["requester_id"] == "user-001"
    assert state["user_query"] == "VPN connection failed"
    assert state["trace_id"] == str(record.trace_id)
    assert state["status"] is TicketStatus.RUNNING
    assert state["step_count"] == 0
    assert state["agent_history"] == []
    assert state["knowledge_refs"] == []
    assert state["action_proposal"] is None
    assert state["tool_results"] == []
    assert state["intent"] is None
    assert state["routing_decision"] is None
    assert state["pending_operation"] is None
    assert state["risk_decision"] is None
    assert state["approval"] is None
    assert state["resolution_mode"] is None
    assert state["final_response"] is None


def test_advance_dispatch_updates_step_count_and_history() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)

    next_state = advance_dispatch(state, actor="supervisor", action="classify_and_route")

    assert next_state["step_count"] == 1
    assert len(next_state["agent_history"]) == 1
    step = next_state["agent_history"][0]
    assert step.step == 1
    assert step.actor == "supervisor"
    assert step.action == "classify_and_route"


def test_advance_dispatch_halts_at_max_steps() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)
    state["step_count"] = MAX_STEPS - 1  # step 14

    next_state = advance_dispatch(state, actor="supervisor", action="step_15")

    assert next_state["step_count"] == MAX_STEPS  # 15
    assert next_state["status"] is TicketStatus.ESCALATED

    # Calling advance_dispatch again on terminal state raises ValueError
    with pytest.raises(ValueError, match="terminal state"):
        advance_dispatch(next_state, actor="knowledge", action="step_16_forbidden")


def test_route_from_supervisor_legal_targets() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)

    state["routing_decision"] = RoutingDecision(
        intent=Intent.NETWORK,
        next_agent="knowledge",
        confidence=0.95,
        escalate=False,
        reason_code=RoutingReason.NEEDS_KNOWLEDGE,
    )
    assert route_from_supervisor(state) == "knowledge"

    state["routing_decision"] = RoutingDecision(
        intent=Intent.ACCOUNT,
        next_agent="action",
        confidence=0.92,
        escalate=False,
        reason_code=RoutingReason.NEEDS_ACTION,
    )
    assert route_from_supervisor(state) == "action"

    state["routing_decision"] = RoutingDecision(
        intent=Intent.QUERY,
        next_agent="finalize",
        confidence=0.98,
        escalate=False,
        reason_code=RoutingReason.COMPLETE,
    )
    assert route_from_supervisor(state) == "finalizer"


def test_supervisor_cannot_repeat_knowledge_handoff_without_progress() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)
    state["routing_decision"] = RoutingDecision(
        intent=Intent.NETWORK,
        next_agent="knowledge",
        confidence=0.95,
        escalate=False,
        reason_code=RoutingReason.NEEDS_KNOWLEDGE,
    )
    advance_dispatch(state, actor="knowledge", action="search_and_ground")

    assert route_from_supervisor(state) == "escalated"


def test_supervisor_repeated_knowledge_route_finishes_when_evidence_exists() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)
    state["routing_decision"] = RoutingDecision(
        intent=Intent.NETWORK,
        next_agent="knowledge",
        confidence=0.95,
        escalate=False,
        reason_code=RoutingReason.NEEDS_KNOWLEDGE,
    )
    state["knowledge_refs"] = [
        KnowledgeRef(
            document_id="doc-1",
            chunk_id="chunk-1",
            title="VPN guide",
            content_excerpt="Use the supported VPN gateway.",
            score=0.9,
        )
    ]
    advance_dispatch(state, actor="knowledge", action="search_and_ground")

    assert route_from_supervisor(state) == "finalizer"


def test_route_from_supervisor_escalation_on_low_confidence_or_flag() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)

    state["routing_decision"] = RoutingDecision(
        intent=Intent.NETWORK,
        next_agent="knowledge",
        confidence=0.5,
        escalate=True,
        reason_code=RoutingReason.LOW_CONFIDENCE,
    )
    assert route_from_supervisor(state) == "escalated"


def test_route_from_knowledge_returns_supervisor() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)
    assert route_from_knowledge(state) == "supervisor"


def test_terminal_state_immutability() -> None:
    record = create_sample_ticket()
    state = initial_ticket_state(record)
    state["status"] = TicketStatus.RESOLVED

    with pytest.raises(ValueError, match="terminal state"):
        advance_dispatch(state, actor="supervisor", action="cannot_run_on_terminal")
