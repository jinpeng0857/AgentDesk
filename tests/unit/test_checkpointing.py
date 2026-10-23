"""Checkpoint serialization must restore only explicitly registered domain types."""

from __future__ import annotations

from pydantic import SecretStr

from agents.schemas import Intent, RoutingDecision, RoutingReason, ToolOutcome, ToolResult
from backend.graph.checkpointing import (
    CHECKPOINT_ALLOWED_MSGPACK_TYPES,
    create_checkpoint_serializer,
)
from backend.graph.nodes import checkpoint_safe_tool_result
from tools.schemas import ResetPasswordResult


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


def test_password_reset_secret_is_removed_before_checkpointing() -> None:
    result = ToolResult(
        request_id="request-001",
        operation_id="operation-001",
        tool_name="reset_password",
        outcome=ToolOutcome.SUCCEEDED,
        data=ResetPasswordResult(
            user_id="user-001",
            temporary_password=SecretStr("temporary-secret"),
            credential_version=2,
        ),
        error_code=None,
        retry_count=0,
        latency_ms=1,
    )

    safe_result = checkpoint_safe_tool_result(result)
    serializer = create_checkpoint_serializer()
    type_name, payload = serializer.dumps_typed(safe_result)
    restored = serializer.loads_typed((type_name, payload))

    assert restored.data is None
    assert "temporary-secret" not in payload.decode("latin1", errors="ignore")
