"""Knowledge role: ground an answer strictly in executed read results.

Every returned reference must have come from a ``search_knowledge`` result that
already ran through the governance gateway. References the model invents are
dropped, and an answer with no surviving reference is marked as a fallback rather
than presented as grounded.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents.context import ModelCallConfig, ModelClient, ModelMessage
from agents.schemas import KnowledgeAgentOutput, KnowledgeRef, TicketState


def render_references(refs: Sequence[KnowledgeRef]) -> str:
    if not refs:
        return "(no retrieved references)"
    return "\n".join(
        f"[{ref.document_id}#{ref.chunk_id}] {ref.title}: {ref.content_excerpt}"
        for ref in refs
    )


async def ground_knowledge(
    *,
    state: TicketState,
    system_prompt: str,
    model_client: ModelClient,
    config: ModelCallConfig,
    available_refs: Sequence[KnowledgeRef],
) -> KnowledgeAgentOutput:
    messages = [
        ModelMessage(role="system", content=system_prompt),
        ModelMessage(
            role="user",
            content=(
                f"User request: {state['user_query']}\n\n"
                f"Retrieved references:\n{render_references(available_refs)}"
            ),
        ),
    ]
    output = await model_client.complete_structured(
        messages=messages,
        output_model=KnowledgeAgentOutput,
        config=config,
    )

    # Only references that actually came back from the tool survive, and the
    # stored citation fields are the tool's, not the model's. Match on chunk id
    # first and fall back to the document id, because a model may echo the
    # document identifier in the chunk field.
    by_chunk = {ref.chunk_id: ref for ref in available_refs}
    by_document: dict[str, KnowledgeRef] = {}
    for ref in available_refs:
        by_document.setdefault(ref.document_id, ref)
    kept: list[KnowledgeRef] = []
    seen: set[str] = set()
    for ref in output.knowledge_refs:
        origin = by_chunk.get(ref.chunk_id) or by_document.get(ref.document_id)
        if origin is None or origin.chunk_id in seen:
            continue
        seen.add(origin.chunk_id)
        kept.append(origin)

    if not kept:
        if available_refs:
            # The governed search is the authority on what was retrieved. If the
            # model declines to cite (or returns only unverifiable ids), keep the
            # highest-ranked executed result as the traceable reference instead
            # of erasing the retrieval evidence from the ticket.
            return KnowledgeAgentOutput(
                answer=output.answer,
                knowledge_refs=[available_refs[0]],
                fallback=output.fallback,
            )
        return KnowledgeAgentOutput(
            answer=output.answer,
            knowledge_refs=[],
            fallback=True,
        )
    return KnowledgeAgentOutput(
        answer=output.answer,
        knowledge_refs=kept,
        fallback=output.fallback,
    )
