"""Compiled LangGraph workflow definition for AgentDesk."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.schemas import TicketState, TicketStatus
from backend.graph.context import GraphContext
from backend.graph.edges import (
    route_from_knowledge,
    route_from_supervisor,
)
from backend.graph.nodes import (
    make_dispatch_node,
    make_knowledge_node,
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
            "finalizer": "finalizer",
            "action": "escalate",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("knowledge", "dispatch_knowledge")
    workflow.add_conditional_edges(
        "dispatch_knowledge",
        route_from_knowledge,
        {
            "supervisor": "finalizer",
            "escalated": "escalate",
        },
    )

    workflow.add_edge("finalizer", "dispatch_finalizer")
    workflow.add_edge("dispatch_finalizer", END)
    workflow.add_edge("escalate", END)

    return workflow.compile(checkpointer=checkpointer)
