"""Compiled LangGraph workflow definition for AgentDesk."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.schemas import TicketState, TicketStatus
from backend.graph.context import GraphContext
from backend.graph.edges import (
    route_from_action,
    route_from_action_execute,
    route_from_approval,
    route_from_knowledge,
    route_from_policy,
    route_from_supervisor,
)
from backend.graph.nodes import (
    make_action_execute_node,
    make_action_propose_node,
    make_approval_node,
    make_dispatch_action_execute_node,
    make_dispatch_node,
    make_dispatch_policy_node,
    make_knowledge_node,
    make_policy_node,
    make_supervisor_finalizer_node,
    make_supervisor_node,
)


def build_ticket_graph(
    context: GraphContext, checkpointer: Any = None
) -> CompiledStateGraph:
    workflow = StateGraph(TicketState)

    # Add nodes
    workflow.add_node("supervisor", make_supervisor_node(context))
    workflow.add_node(
        "dispatch_supervisor",
        make_dispatch_node("supervisor", "classify_and_route"),
    )

    workflow.add_node("knowledge", make_knowledge_node(context))
    workflow.add_node(
        "dispatch_knowledge",
        make_dispatch_node("knowledge", "search_and_ground"),
    )

    workflow.add_node("action", make_action_propose_node(context))
    workflow.add_node(
        "dispatch_action",
        make_dispatch_node("action", "propose_operation"),
    )

    workflow.add_node("policy", make_policy_node(context))
    workflow.add_node("dispatch_policy", make_dispatch_policy_node())

    workflow.add_node("approval", make_approval_node(context))
    workflow.add_node(
        "dispatch_approval",
        make_dispatch_node("approval", "evaluate_approval"),
    )

    workflow.add_node("action_execute", make_action_execute_node(context))
    workflow.add_node(
        "dispatch_action_execute",
        make_dispatch_action_execute_node(),
    )

    workflow.add_node(
        "reject",
        make_dispatch_node(
            "approval", "reject_approval", next_status=TicketStatus.REJECTED
        ),
    )

    workflow.add_node(
        "reject_request",
        make_dispatch_node(
            "dispatcher", "reject_request", next_status=TicketStatus.REJECTED
        ),
    )

    workflow.add_node("finalizer", make_supervisor_finalizer_node(context))
    workflow.add_node(
        "dispatch_finalizer",
        make_dispatch_node(
            "supervisor", "finalize_response", next_status=TicketStatus.RESOLVED
        ),
    )

    workflow.add_node(
        "escalate",
        make_dispatch_node(
            "dispatcher", "escalate_ticket", next_status=TicketStatus.ESCALATED
        ),
    )

    # Add edges
    workflow.add_edge(START, "supervisor")
    workflow.add_edge("supervisor", "dispatch_supervisor")

    workflow.add_conditional_edges(
        "dispatch_supervisor",
        route_from_supervisor,
        {
            "knowledge": "knowledge",
            "action": "action",
            "finalizer": "finalizer",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("knowledge", "dispatch_knowledge")
    workflow.add_conditional_edges(
        "dispatch_knowledge",
        route_from_knowledge,
        {
            "supervisor": "supervisor",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("action", "dispatch_action")
    workflow.add_conditional_edges(
        "dispatch_action",
        route_from_action,
        {
            "policy": "policy",
            "action_execute": "action_execute",
            "supervisor": "supervisor",
            "finalizer": "finalizer",
            "rejected": "reject_request",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("policy", "dispatch_policy")
    workflow.add_conditional_edges(
        "dispatch_policy",
        route_from_policy,
        {
            "approval": "approval",
            "action_execute": "action_execute",
            "rejected": "reject_request",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("approval", "dispatch_approval")
    workflow.add_conditional_edges(
        "dispatch_approval",
        route_from_approval,
        {
            "action_execute": "action_execute",
            "rejected": "reject",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("action_execute", "dispatch_action_execute")
    workflow.add_conditional_edges(
        "dispatch_action_execute",
        route_from_action_execute,
        {
            "supervisor": "supervisor",
            "rejected": "reject_request",
            "escalated": "escalate",
        },
    )
    workflow.add_edge("reject", END)
    workflow.add_edge("reject_request", END)
    workflow.add_edge("finalizer", "dispatch_finalizer")
    workflow.add_edge("dispatch_finalizer", END)
    workflow.add_edge("escalate", END)

    return workflow.compile(checkpointer=checkpointer)

