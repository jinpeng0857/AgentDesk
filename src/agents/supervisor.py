"""Supervisor role: classify the ticket, choose the next role, or finalize.

The Supervisor never calls, names or executes a tool, and never assigns risk,
authorization or operation identifiers. Tool schemas are therefore never placed
in its prompts.
"""

from __future__ import annotations

import json

from agents.context import ModelCallConfig, ModelClient, ModelMessage
from agents.schemas import (
    FinalResponse,
    RoutingDecision,
    SupervisorOutput,
    TicketState,
)


def render_verified_context(state: TicketState) -> str:
    """Render only executed results; never a tool catalogue or instructions."""

    def safe_mapping(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: safe_mapping(item)
                for key, item in value.items()
                if not any(
                    marker in key.lower()
                    for marker in ("password", "secret", "token", "api_key", "card")
                )
            }
        if isinstance(value, list):
            return [safe_mapping(item) for item in value]
        return value

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
            + (
                " | verified result: "
                + json.dumps(
                    safe_mapping(result.data.model_dump(mode="json")),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if result.data is not None
                else ""
            )
            for result in state["tool_results"]
        )
    else:
        lines.append("Executed tool outcomes: none")
    risk_decision = state.get("risk_decision")
    if risk_decision is not None:
        lines.append(
            f"Policy decision: allowed={risk_decision.allowed}; "
            f"risk={risk_decision.risk_level}; "
            f"require_human={risk_decision.require_human}; "
            f"reason={risk_decision.reason_code}"
        )
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
        ModelMessage(role="user", content=render_verified_context(state)),
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
