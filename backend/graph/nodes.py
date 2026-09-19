"""LangGraph node closures with strict dependency injection and field ownership."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from agents.context import ModelCallConfig
from agents.knowledge import ground_knowledge
from agents.schemas import (
    FinalResponse,
    KnowledgeRef,
    ResolutionMode,
    RoutingDecision,
    TicketState,
    TicketStatus,
    ToolOutcome,
)
from agents.supervisor import finalize_ticket, run_supervisor
from backend.graph.context import GraphContext
from backend.graph.edges import advance_dispatch
from backend.graph.state import validate_node_output_keys
from backend.integrations.prompt_loader import build_system_message
from tools.schemas import SearchKnowledgeResult

AsyncNode = Callable[[TicketState], Awaitable[dict[str, Any]]]
SyncNode = Callable[[TicketState], dict[str, Any]]


def make_supervisor_node(context: GraphContext) -> AsyncNode:
    async def supervisor_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["supervisor"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])
        output = await run_supervisor(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
        )
        if not isinstance(output, RoutingDecision):
            raise TypeError(
                f"Supervisor node expected RoutingDecision, got {type(output).__name__}"
            )

        updates = {
            "intent": output.intent,
            "routing_decision": output,
        }
        validate_node_output_keys("supervisor", updates)
        return updates

    return supervisor_node


def make_knowledge_node(context: GraphContext) -> AsyncNode:
    async def knowledge_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["knowledge"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])

        available_refs: list[KnowledgeRef] = []
        if context.tool_executor is not None:
            tool_result = await context.tool_executor.execute(
                agent_id="knowledge",
                tool_name="search_knowledge",
                raw_args={"query": state["user_query"], "top_k": 3},
            )
            if (
                tool_result.outcome is ToolOutcome.SUCCEEDED
                and tool_result.data is not None
            ):
                search_data = cast(SearchKnowledgeResult, tool_result.data)
                available_refs = [
                    KnowledgeRef(
                        document_id=item.document_id,
                        chunk_id=item.chunk_id,
                        title=item.title,
                        content_excerpt=item.content_excerpt,
                        score=item.score,
                    )
                    for item in search_data.refs
                ]

        output = await ground_knowledge(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
            available_refs=available_refs,
        )

        updates = {"knowledge_refs": output.knowledge_refs}
        validate_node_output_keys("knowledge", updates)
        return updates

    return knowledge_node


def make_supervisor_finalizer_node(context: GraphContext) -> AsyncNode:
    async def finalizer_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["supervisor"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])
        output = await finalize_ticket(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
        )
        if isinstance(output, FinalResponse):
            updates = {
                "resolution_mode": output.resolution_mode,
                "final_response": output.final_response,
            }
        else:
            updates = {
                "resolution_mode": ResolutionMode.AUTOMATED,
                "final_response": "Ticket finalized.",
            }

        validate_node_output_keys("supervisor_finalizer", updates)
        return updates

    return finalizer_node


def make_dispatch_node(
    actor: str,
    action: str,
    next_status: TicketStatus | None = None,
) -> SyncNode:
    def dispatch_node(state: TicketState) -> dict[str, Any]:
        next_state = advance_dispatch(
            state,
            actor=cast(Any, actor),
            action=action,
            next_status=next_status,
        )
        updates = {
            "step_count": next_state["step_count"],
            "agent_history": next_state["agent_history"],
            "status": next_state["status"],
        }
        validate_node_output_keys("dispatcher", updates)
        return updates

    return dispatch_node
