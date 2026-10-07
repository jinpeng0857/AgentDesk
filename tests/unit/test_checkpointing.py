"""Checkpoint serialization must restore only explicitly registered domain types."""

from __future__ import annotations

from agents.schemas import Intent, RoutingDecision, RoutingReason
from backend.graph.checkpointing import (
    CHECKPOINT_ALLOWED_MSGPACK_TYPES,
    create_checkpoint_serializer,
)


def test_checkpoint_serializer_round_trips_registered_state_types() -> None:
    serializer = create_checkpoint_serializer()
    decision = RoutingDecision(
        intent=Intent.NETWORK,
        next_agent="knowledge",
        confidence=0.95,
        escalate=False,
        reason_code=RoutingReason.NEEDS_KNOWLEDGE,
    )

    type_name, payload = serializer.dumps_typed(
        {"intent": Intent.NETWORK, "routing_decision": decision}
    )
    restored = serializer.loads_typed((type_name, payload))

    assert restored == {"intent": Intent.NETWORK, "routing_decision": decision}
    assert isinstance(restored["intent"], Intent)
    assert isinstance(restored["routing_decision"], RoutingDecision)
    assert serializer.pickle_fallback is False


def test_checkpoint_allow_list_is_scoped_to_domain_contract_modules() -> None:
    assert CHECKPOINT_ALLOWED_MSGPACK_TYPES
    assert {module for module, _name in CHECKPOINT_ALLOWED_MSGPACK_TYPES} == {
        "agents.schemas",
        "tools.schemas",
    }
