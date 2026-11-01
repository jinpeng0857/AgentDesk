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
    ToolOutcome,
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
        if any(
            step.actor == "knowledge"
            for step in state.get("agent_history", [])
        ):
            return "finalizer" if state.get("knowledge_refs") else "escalated"
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
    """Route write proposals to Policy and read proposals to the execution gateway."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    pending_operation = state.get("pending_operation")
    action_proposal = state.get("action_proposal")
    proposed_tool = (
        pending_operation.tool_name
        if pending_operation is not None
        else action_proposal.tool_name if action_proposal is not None else None
    )
    if proposed_tool is not None and any(
        result.tool_name == proposed_tool
        and result.outcome in (ToolOutcome.SUCCEEDED, ToolOutcome.CACHE_HIT)
        for result in state.get("tool_results", [])
    ):
        # A repeated proposal is not a new operation merely because Action minted
        # a fresh operation_id. Stop the model bounce deterministically; never
        # execute the already-completed Tool again.
        return "finalizer"

    if pending_operation is not None:
        return "policy"
    if action_proposal is not None:
        return "action_execute"
    # Action ran but deterministically produced no proposal: the request was
    # refused (cross-user target or unauthorized tool directive). This is a
    # business rejection, not an infrastructure escalation.
    return "rejected"


def route_from_policy(state: TicketState) -> str:
    """Route from policy evaluation: to approval (high risk) or action_execute (allowed)."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    risk = state.get("risk_decision")
    if risk is None:
        return "escalated"
    if not risk.allowed:
        # A deterministic Policy denial is a reviewed business outcome. It must
        # end in the explicit rejected terminal with zero side effects.
        return "rejected"

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


def route_from_action_execute(state: TicketState) -> str:
    """Return successful tool results to Supervisor; stop on failures."""
    if state["step_count"] >= MAX_STEPS or state["status"] in TERMINAL_STATUSES:
        return "escalated"

    tool_results = state.get("tool_results", [])
    if not tool_results:
        return "escalated"

    if tool_results[-1].outcome in (ToolOutcome.SUCCEEDED, ToolOutcome.CACHE_HIT):
        return "supervisor"
    if tool_results[-1].outcome is ToolOutcome.REJECTED:
        # Governance refused the attempt (unknown/unauthorized tool or denied
        # operation). F-04 requires the rejected terminal, not escalation.
        return "rejected"
    return "escalated"
