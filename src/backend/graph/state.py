"""Ticket state definitions and field ownership enforcement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents.schemas import (
    Intent,
    ResolutionMode,
    TicketState,
    TicketStatus,
)
from backend.db.models import TicketRecord

NODE_OWNED_KEYS: Mapping[str, frozenset[str]] = {
    "supervisor": frozenset({"intent", "routing_decision"}),
    "supervisor_finalizer": frozenset({"resolution_mode", "final_response"}),
    "knowledge": frozenset({"knowledge_refs"}),
    "action_propose": frozenset({"action_proposal", "pending_operation"}),
    "action_execute": frozenset({"tool_results"}),
    "policy": frozenset({"risk_decision"}),
    "approval": frozenset({"approval"}),
    "dispatcher": frozenset({"status", "agent_history", "step_count"}),
    "api_ingress": frozenset(
        {"ticket_id", "thread_id", "requester_id", "user_query", "trace_id"}
    ),
}


def validate_node_output_keys(node_name: str, updates: Mapping[str, Any]) -> None:
    """Enforce AGENTS.md section 2.1 state field ownership."""
    allowed = NODE_OWNED_KEYS.get(node_name)
    if allowed is None:
        raise ValueError(f"Unknown node name for state ownership check: {node_name}")
    invalid_keys = set(updates.keys()) - allowed
    if invalid_keys:
        raise ValueError(
            f"Node '{node_name}' is not allowed to write state keys: {sorted(invalid_keys)}. "
            f"Allowed keys: {sorted(allowed)}"
        )


def initial_ticket_state(record: TicketRecord) -> TicketState:
    """Initialize a pristine TicketState from a persisted TicketRecord."""
    return {
        "ticket_id": str(record.id),
        "thread_id": record.thread_id,
        "requester_id": record.requester_id,
        "user_query": record.user_query,
        "trace_id": str(record.trace_id),
        "status": TicketStatus(record.status) if record.status else TicketStatus.RUNNING,
        "intent": Intent(record.intent) if record.intent else None,
        "routing_decision": None,
        "knowledge_refs": [],
        "action_proposal": None,
        "pending_operation": None,
        "tool_results": [],
        "risk_decision": None,
        "approval": None,
        "agent_history": [],
        "step_count": record.step_count or 0,
        "resolution_mode": (
            ResolutionMode(record.resolution_mode) if record.resolution_mode else None
        ),
        "final_response": record.final_response,
    }
