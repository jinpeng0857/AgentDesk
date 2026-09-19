"""Unit tests verifying strict field ownership per AGENTS.md section 2.1."""

import pytest

from backend.graph.state import NODE_OWNED_KEYS, validate_node_output_keys


def test_node_owned_keys_covers_all_nodes() -> None:
    assert "supervisor" in NODE_OWNED_KEYS
    assert "supervisor_finalizer" in NODE_OWNED_KEYS
    assert "knowledge" in NODE_OWNED_KEYS
    assert "action_propose" in NODE_OWNED_KEYS
    assert "action_execute" in NODE_OWNED_KEYS
    assert "policy" in NODE_OWNED_KEYS
    assert "approval" in NODE_OWNED_KEYS
    assert "dispatcher" in NODE_OWNED_KEYS


def test_validate_node_output_keys_accepts_valid_updates() -> None:
    # supervisor can write intent and routing_decision
    validate_node_output_keys("supervisor", {"intent": "network", "routing_decision": None})

    # knowledge can write knowledge_refs
    validate_node_output_keys("knowledge", {"knowledge_refs": []})

    # action_propose can write pending_operation
    validate_node_output_keys("action_propose", {"pending_operation": None})

    # action_execute can write tool_results
    validate_node_output_keys("action_execute", {"tool_results": []})

    # policy can write risk_decision
    validate_node_output_keys("policy", {"risk_decision": None})

    # approval can write approval
    validate_node_output_keys("approval", {"approval": None})

    # supervisor_finalizer can write resolution_mode and final_response
    validate_node_output_keys(
        "supervisor_finalizer", {"resolution_mode": "automated", "final_response": "done"}
    )


def test_validate_node_output_keys_rejects_unowned_field_writes() -> None:
    # Supervisor must not write tool_results or pending_operation
    with pytest.raises(ValueError, match="not allowed to write"):
        validate_node_output_keys("supervisor", {"tool_results": []})

    # Knowledge must not write status or step_count
    with pytest.raises(ValueError, match="not allowed to write"):
        validate_node_output_keys("knowledge", {"status": "resolved"})

    # Action must not write step_count or routing_decision
    with pytest.raises(ValueError, match="not allowed to write"):
        validate_node_output_keys("action_execute", {"step_count": 5})

    # Policy must not write final_response
    with pytest.raises(ValueError, match="not allowed to write"):
        validate_node_output_keys("policy", {"final_response": "rejected"})
