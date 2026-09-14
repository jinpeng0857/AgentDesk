from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

EXPECTED_TOOL_CONFIG = {
    "search_knowledge": ("read", "low", True, 8.0),
    "query_user": ("read", "low", True, 3.0),
    "query_order": ("read", "low", True, 3.0),
    "check_service": ("read", "low", True, 5.0),
    "create_ticket": ("write", "low", True, 5.0),
    "request_access": ("write", "medium", True, 5.0),
    "reset_password": ("write", "medium", True, 10.0),
    "process_refund": ("write", "medium", True, 10.0),
}
TOOL_SPEC_FIELDS = {
    "name",
    "description",
    "parameters",
    "result_schema",
    "side_effects",
    "risk_level",
    "idempotent",
    "timeout_s",
}


def _registry_module():
    return importlib.import_module("tools.registry")


def _registry():
    registry = getattr(_registry_module(), "TOOL_REGISTRY", None)
    assert registry is not None, "TOOL_REGISTRY is missing"
    return registry


def test_registry_is_closed_and_delete_user_is_absent() -> None:
    registry = _registry()

    assert set(registry) == set(EXPECTED_TOOL_CONFIG)
    assert "delete_user" not in registry


def test_agent_whitelist_enforces_role_boundaries() -> None:
    module = _registry_module()
    whitelist = getattr(module, "AGENT_TOOL_WHITELIST", None)
    assert whitelist is not None, "AGENT_TOOL_WHITELIST is missing"

    assert whitelist == {
        "supervisor": (),
        "knowledge": ("search_knowledge",),
        "action": (
            "query_user",
            "query_order",
            "check_service",
            "create_ticket",
            "request_access",
            "reset_password",
            "process_refund",
        ),
        "policy": (),
    }


def test_tool_specs_have_exact_public_fields_and_frozen_config() -> None:
    registry = _registry()

    for spec in registry.values():
        assert set(spec.model_dump()) == TOOL_SPEC_FIELDS
        with pytest.raises(ValidationError):
            spec.timeout_s = 99.0


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    EXPECTED_TOOL_CONFIG.items(),
)
def test_tool_risk_and_reliability_config_is_explicit(
    tool_name: str,
    expected: tuple[str, str, bool, float],
) -> None:
    spec = _registry()[tool_name]

    assert (
        spec.side_effects,
        spec.risk_level,
        spec.idempotent,
        spec.timeout_s,
    ) == expected


def test_tool_descriptions_are_independently_reviewable() -> None:
    registry = _registry()

    for spec in registry.values():
        normalized = spec.description.lower()
        assert f"side effects: {spec.side_effects}" in normalized
        assert "required inputs:" in normalized
        assert "result:" in normalized
        assert "sensitive data:" in normalized
        assert "approval:" in normalized


def test_parameter_and_result_schemas_come_from_declared_models() -> None:
    module = _registry_module()
    registry = _registry()
    model_pairs = getattr(module, "TOOL_MODEL_PAIRS", None)
    assert model_pairs is not None, "TOOL_MODEL_PAIRS is missing"

    assert set(model_pairs) == set(registry)
    for name, spec in registry.items():
        input_model, result_model = model_pairs[name]
        assert spec.parameters == input_model.model_json_schema()
        assert spec.result_schema == result_model.model_json_schema()
