"""Validate Agent and Prompt contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import yaml
from pydantic import TypeAdapter

import agents.schemas as agent_schemas
from tools.registry import AGENT_TOOL_WHITELIST

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROLES = ("supervisor", "knowledge", "action", "policy")
PROMPT_FIELDS = {"version", "role", "responsibility", "constraints", "output_schema"}
CONTRACT_FIELDS = {
    "version",
    "agent_id",
    "input_models",
    "output_models",
    "allowed_tools",
    "state_writes",
    "invariants",
    "failure_codes",
}
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
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
FORBIDDEN_ADVISORY_LANGUAGE = ("shouldn't", "try to avoid", "尽量")


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return cast(dict[str, object], loaded)


def load_prompts(
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, dict[str, object]]:
    return {
        role: _load_yaml_mapping(repository_root / "resources" / "prompts" / f"{role}.yaml")
        for role in ROLES
    }


def load_agent_contracts(
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, dict[str, object]]:
    contract_root = repository_root / "specs" / "agent_contracts"
    return {
        role: _load_yaml_mapping(contract_root / f"{role}.yaml")
        for role in ROLES
    }


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return cast(list[str], value)


def _validate_prompt(role: str, prompt: dict[str, object]) -> list[str]:
    issues: list[str] = []
    if set(prompt) != PROMPT_FIELDS:
        issues.append(f"{role} prompt fields must equal {sorted(PROMPT_FIELDS)}")
        return issues

    version = prompt["version"]
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        issues.append(f"{role} prompt version must use semantic versioning")

    for field_name in ("role", "responsibility", "constraints"):
        value = prompt[field_name]
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{role} prompt {field_name} must be a non-empty string")

    constraints = prompt["constraints"]
    if isinstance(constraints, str):
        lowered = constraints.lower()
        if "must" not in lowered and "never" not in lowered and "only" not in lowered:
            issues.append(f"{role} prompt constraints need absolute language")
        for forbidden in FORBIDDEN_ADVISORY_LANGUAGE:
            if forbidden in lowered:
                issues.append(f"{role} prompt contains advisory language: {forbidden}")

    output_schema = prompt["output_schema"]
    if not isinstance(output_schema, dict):
        issues.append(f"{role} prompt output_schema must be a mapping")
        return issues

    model_name = output_schema.get("model")
    strict = output_schema.get("strict")
    if model_name != EXPECTED_PROMPT_OUTPUTS[role] or strict is not True:
        issues.append(f"{role} prompt output_schema does not match its frozen model")
        return issues

    model_type = getattr(agent_schemas, str(model_name), None)
    if model_type is None:
        issues.append(f"{role} prompt output model is missing: {model_name}")
    else:
        TypeAdapter(model_type).json_schema()
    return issues


def _validate_contract(role: str, contract: dict[str, object]) -> list[str]:
    issues: list[str] = []
    if set(contract) != CONTRACT_FIELDS:
        issues.append(f"{role} contract fields must equal {sorted(CONTRACT_FIELDS)}")
        return issues

    version = contract["version"]
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        issues.append(f"{role} contract version must use semantic versioning")
    if contract["agent_id"] != role:
        issues.append(f"{role} contract agent_id must equal its filename")

    allowed_tools = _string_list(contract["allowed_tools"])
    if allowed_tools is None:
        issues.append(f"{role} allowed_tools must be a string list")
    elif tuple(allowed_tools) != AGENT_TOOL_WHITELIST[role]:
        issues.append(f"{role} allowed_tools does not match the code whitelist")

    state_writes = _string_list(contract["state_writes"])
    if state_writes != EXPECTED_STATE_WRITES[role]:
        issues.append(f"{role} state_writes does not match field ownership")

    for field_name in ("input_models", "output_models"):
        model_names = _string_list(contract[field_name])
        if not model_names:
            issues.append(f"{role} {field_name} must be a non-empty string list")
            continue
        for model_name in model_names:
            if getattr(agent_schemas, model_name, None) is None:
                issues.append(f"{role} references missing Agent model {model_name}")

    for field_name in ("invariants", "failure_codes"):
        values = _string_list(contract[field_name])
        if not values:
            issues.append(f"{role} {field_name} must be a non-empty string list")

    return issues


def validate_agent_contracts(
    repository_root: Path = REPOSITORY_ROOT,
) -> list[str]:
    prompts = load_prompts(repository_root)
    contracts = load_agent_contracts(repository_root)
    issues: list[str] = []
    for role in ROLES:
        issues.extend(_validate_prompt(role, prompts[role]))
        issues.extend(_validate_contract(role, contracts[role]))
    return sorted(issues)


def main() -> int:
    issues = validate_agent_contracts()
    if issues:
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Agent and Prompt contracts are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
