from __future__ import annotations

import importlib

EXPECTED_STATE_WRITES = {
    "supervisor": ["intent", "routing_decision", "resolution_mode", "final_response"],
    "knowledge": ["knowledge_refs"],
    "action": ["action_proposal", "pending_operation", "tool_results"],
    "policy": ["risk_decision"],
}
EXPECTED_PROMPT_OUTPUTS = {
    "supervisor": "SupervisorOutput",
    "knowledge": "KnowledgeAgentOutput",
    "action": "ActionProposal",
    "policy": "PolicyExplanation",
}


def _validator():
    module = importlib.import_module("scripts.maintenance.validate_agent_contracts")
    validator = getattr(module, "validate_agent_contracts", None)
    assert validator is not None, "validate_agent_contracts is missing"
    return validator


def _loader(name: str):
    module = importlib.import_module("scripts.maintenance.validate_agent_contracts")
    loader = getattr(module, name, None)
    assert loader is not None, f"{name} is missing"
    return loader


def test_all_agent_and_prompt_contracts_are_valid() -> None:
    issues = _validator()()

    assert issues == []


def test_contract_state_ownership_matches_architecture() -> None:
    contracts = _loader("load_agent_contracts")()

    assert {
        role: contract["state_writes"] for role, contract in contracts.items()
    } == EXPECTED_STATE_WRITES


def test_prompt_output_models_are_explicit() -> None:
    prompts = _loader("load_prompts")()

    assert {
        role: prompt["output_schema"]["model"] for role, prompt in prompts.items()
    } == EXPECTED_PROMPT_OUTPUTS


def test_supervisor_prompt_has_fixed_intent_precedence() -> None:
    supervisor = _loader("load_prompts")()["supervisor"]
    responsibility = " ".join(str(supervisor["responsibility"]).split())

    precedence = [
        "1. high_risk:",
        "2. permission:",
        "3. account:",
        "4. network:",
        "5. query:",
    ]
    positions = [responsibility.index(intent) for intent in precedence]
    assert positions == sorted(positions)
    assert "every refund request is high_risk regardless" in responsibility.lower()
    assert "policy alone decides allow, risk and approval" in responsibility.lower()
    assert "sql-injection attempt" in responsibility.lower()
    assert "duplicate or concurrent approval, resume, replay" in responsibility.lower()
    assert "read-only queries about access approval owners" in responsibility.lower()
    assert "revocation periods" in responsibility.lower()
    assert "even when asking for a policy/sop" in responsibility.lower()
    assert "duplicate message-queue delivery" in responsibility.lower()
    assert "password-complexity policy" in responsibility.lower()
    assert "sso-credential sop" in responsibility.lower()
    assert "query an order's current status or refundable amount" in responsibility.lower()
    assert "issue/process/execute a refund" in responsibility.lower()
    assert "pac/proxy configuration address" in responsibility.lower()
    assert "switch optical-loss alarm" in responsibility.lower()
    assert "worker recovers from a checkpoint after a write" in responsibility.lower()


def test_supervisor_contract_freezes_security_and_access_intent_boundaries() -> None:
    invariants = _loader("load_agent_contracts")()["supervisor"]["invariants"]
    normalized_invariants = [str(item).lower() for item in invariants]

    assert any(
        "sql-injection or destructive data-change attempts" in item
        for item in normalized_invariants
    )
    assert any("read-only access-process queries" in item for item in normalized_invariants)
