"""Supervisor role: classify the ticket, choose the next role, or finalize.

The Supervisor never calls, names or executes a tool, and never assigns risk,
authorization or operation identifiers. Tool schemas are therefore never placed
in its prompts.
"""

from __future__ import annotations

from agents.context import ModelCallConfig, ModelClient, ModelMessage
from agents.schemas import (
    FinalResponse,
    RoutingDecision,
    SupervisorOutput,
    TicketState,
)


def render_verified_context(state: TicketState) -> str:
    """Render only executed results; never a tool catalogue or instructions."""
    lines = [f"User request: {state['user_query']}"]
    if state["intent"] is not None:
        lines.append(f"Classified intent: {state['intent']}")
    if state["knowledge_refs"]:
        lines.append("Retrieved references:")
        lines.extend(
            f"- {ref.document_id}#{ref.chunk_id} ({ref.title}): {ref.content_excerpt}"
            for ref in state["knowledge_refs"]
        )
    else:
        lines.append("Retrieved references: none")
    if state["tool_results"]:
        lines.append("Executed tool outcomes:")
        lines.extend(
            f"- {result.tool_name}: {result.outcome}"
            + (f" ({result.error_code})" if result.error_code else "")
            for result in state["tool_results"]
        )
    else:
        lines.append("Executed tool outcomes: none")
    return "\n".join(lines)


async def run_supervisor(
    *,
    state: TicketState,
    system_prompt: str,
    model_client: ModelClient,
    config: ModelCallConfig,
) -> SupervisorOutput:
    """Classify the ticket and choose the next permitted role."""
    messages = [
        ModelMessage(role="system", content=system_prompt),
        ModelMessage(role="user", content=state["user_query"]),
    ]
    return await model_client.complete_structured(
        messages=messages,
        output_model=RoutingDecision,
        config=config,
    )


async def finalize_ticket(
    *,
    state: TicketState,
    system_prompt: str,
    model_client: ModelClient,
    config: ModelCallConfig,
) -> SupervisorOutput:
    """Summarize verified results into the user-facing response."""
    messages = [
        ModelMessage(role="system", content=system_prompt),
        ModelMessage(role="user", content=render_verified_context(state)),
    ]
    return await model_client.complete_structured(
        messages=messages,
        output_model=FinalResponse,
        config=config,
    )
