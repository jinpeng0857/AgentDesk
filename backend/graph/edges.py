"""Deterministic routing edges and dispatcher logic.

This module is strictly deterministic. Per AGENTS.md H-04 and H-22, it must NEVER
import ModelClient, LLM adapters, or Tool callables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from agents.schemas import (
    AgentStep,
    ApprovalStatus,
    RiskLevel,
    TicketState,
    TicketStatus,
)

MAX_STEPS = 15

TERMINAL_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.RESOLVED,
        TicketStatus.ESCALATED,
        TicketStatus.REJECTED,
        TicketStatus.FAILED,
        TicketStatus.EXPIRED,
    }
)


def advance_dispatch(
    state: TicketState,
    *,
    actor: Literal["supervisor", "knowledge", "action", "policy", "approval", "dispatcher"],
    action: str,
    next_status: TicketStatus | None = None,
) -> TicketState:
    """Advance the ticket lifecycle by one step.

    Dispatcher is the sole writer of status, agent_history, and step_count (H-03).
    Halts immediately and transitions to escalated when MAX_STEPS (15) is reached (H-07).
    """
    current_status = state["status"]
    if current_status in TERMINAL_STATUSES:
        if current_status == TicketStatus.ESCALATED and actor == "dispatcher":
            return state
        raise ValueError(
            f"Cannot advance ticket in terminal state '{current_status}'. "
            "Terminal states are immutable."
        )

    current_steps = state["step_count"]
    if current_steps >= MAX_STEPS:
        state["status"] = TicketStatus.ESCALATED
        return state

    new_step_count = current_steps + 1
    state["step_count"] = new_step_count

    step_record = AgentStep(
        step=new_step_count,
        actor=actor,
        action=action,
        timestamp=datetime.now(UTC),
    )
    # Append strictly to agent_history
    history = list(state.get("agent_history", []))
    history.append(step_record)
    state["agent_history"] = history

    if new_step_count >= MAX_STEPS:
        state["status"] = TicketStatus.ESCALATED
    elif next_status is not None:
        state["status"] = next_status
    elif state["status"] == TicketStatus.QUEUED:
        state["status"] = TicketStatus.RUNNING

    return state


def route_from_supervisor(state: TicketState) -> str:
    """Determine the next graph destination from supervisor's decision."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    decision = state.get("routing_decision")
    if decision is None:
        return "escalated"

    if decision.escalate or decision.confidence < 0.7:
        return "escalated"

    if decision.next_agent == "knowledge":
        return "knowledge"
    if decision.next_agent == "action":
        return "action"
    return "finalizer"


def route_from_knowledge(state: TicketState) -> str:
    """Route from knowledge agent back to supervisor."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"
    return "supervisor"


def route_from_action(state: TicketState) -> str:
    """Route from action agent: to policy if propose, or to supervisor if executed."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    if state.get("pending_operation") is not None:
        return "policy"
    return "supervisor"


def route_from_policy(state: TicketState) -> str:
    """Route from policy evaluation: to approval (high risk) or action_execute (allowed)."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    risk = state.get("risk_decision")
    if risk is None or not risk.allowed:
        return "escalated"

    if risk.risk_level is RiskLevel.HIGH or risk.require_human:
        return "approval"

    return "action_execute"


def route_from_approval(state: TicketState) -> str:
    """Route from human-in-the-loop approval."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    approval = state.get("approval")
    if approval is None:
        return "escalated"

    if approval.status is ApprovalStatus.APPROVED:
        return "action_execute"
    if approval.status is ApprovalStatus.REJECTED:
        return "rejected"

    return "escalated"
