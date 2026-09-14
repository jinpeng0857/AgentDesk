from __future__ import annotations

import importlib

EXPECTED_STATE_WRITES = {
    "supervisor": ["intent", "routing_decision", "resolution_mode", "final_response"],
    "knowledge": ["knowledge_refs"],
    "action": ["pending_operation", "tool_results"],
    "policy": ["risk_decision"],
}
EXPECTED_PROMPT_OUTPUTS = {
    "supervisor": "SupervisorOutput",
    "knowledge": "KnowledgeAgentOutput",
    "action": "ActionProposal",
    "policy": "PolicyExplanation",
}


def _validator():
    module = importlib.import_module("scripts.validate_agent_contracts")
    validator = getattr(module, "validate_agent_contracts", None)
    assert validator is not None, "validate_agent_contracts is missing"
    return validator


def _loader(name: str):
    module = importlib.import_module("scripts.validate_agent_contracts")
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
