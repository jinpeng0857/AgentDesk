"""Action role: select exactly one enabled read capability.

Phase 1 exposes read capabilities only. The Action role never mints operation
identifiers, argument hashes or approvals, and it never executes a tool: it
returns a proposal that the governance gateway decides on.
"""

from __future__ import annotations

from agents.context import ModelCallConfig, ModelClient, ModelMessage
from agents.policy import PHASE1_ACTION_READ_TOOLS
from agents.schemas import ActionProposal, TicketState
from tools.registry import TOOL_REGISTRY


def read_tool_catalogue() -> str:
    """Describe only the capabilities enabled in this phase."""
    lines = ["Enabled read capabilities (select exactly one, or none):"]
    lines.extend(
        f"- {name}: {TOOL_REGISTRY[name].description}"
        for name in PHASE1_ACTION_READ_TOOLS
    )
    return "\n".join(lines)


async def select_action(
    *,
    state: TicketState,
    system_prompt: str,
    model_client: ModelClient,
    config: ModelCallConfig,
) -> ActionProposal:
    messages = [
        ModelMessage(role="system", content=system_prompt),
        ModelMessage(
            role="user",
            content=f"User request: {state['user_query']}\n\n{read_tool_catalogue()}",
        ),
    ]
    return await model_client.complete_structured(
        messages=messages,
        output_model=ActionProposal,
        config=config,
    )
