from __future__ import annotations

import importlib
from collections import Counter

from tools.registry import TOOL_REGISTRY


def _dataset_module():
    return importlib.import_module("scripts.validate_golden_dataset")


def _dataset():
    loader = getattr(_dataset_module(), "load_golden_dataset", None)
    assert loader is not None, "load_golden_dataset is missing"
    return loader()


def test_smoke_dataset_has_twenty_unique_reviewed_cases() -> None:
    dataset = _dataset()

    assert dataset.version == "0.2.0"
    assert dataset.level == "smoke"
    assert dataset.review_status == "reviewed"
    assert len(dataset.cases) == 20
    assert len({case.case_id for case in dataset.cases}) == 20


def test_each_intent_has_four_cases() -> None:
    distribution = Counter(case.expected_intent.value for case in _dataset().cases)

    assert distribution == {
        "network": 4,
        "account": 4,
        "permission": 4,
        "query": 4,
        "high_risk": 4,
    }


def test_all_expected_tools_are_registered() -> None:
    expected_tools = {
        tool_name for case in _dataset().cases for tool_name in case.expected_tools
    }

    assert expected_tools <= set(TOOL_REGISTRY)
    assert "delete_user" not in expected_tools


def test_refund_boundary_and_security_cases_are_present() -> None:
    dataset = _dataset()
    queries = [case.query for case in dataset.cases]
    tags = {tag for case in dataset.cases for tag in case.tags}

    assert any("199.99" in query for query in queries)
    assert any("200.00" in query for query in queries)
    assert {"authorization", "prompt_injection", "boundary"} <= tags


def test_high_risk_and_write_handoff_invariants_hold() -> None:
    write_tools = {
        name for name, spec in TOOL_REGISTRY.items() if spec.side_effects == "write"
    }
    for case in _dataset().cases:
        nodes = {
            handoff.source for handoff in case.expected_handoffs
        } | {handoff.target for handoff in case.expected_handoffs}

        if case.expected_require_human:
            assert case.expected_risk.value == "high"
        if case.expected_outcome.value == "waiting_approval":
            assert case.expected_require_human is True
        if write_tools.intersection(case.expected_tools):
            assert "policy" in nodes
        if case.relevant_document_ids:
            assert "search_knowledge" in case.expected_tools
        assert case.min_business_steps > 0


def test_dataset_validator_reports_no_issues() -> None:
    validator = getattr(_dataset_module(), "validate_golden_dataset", None)
    assert validator is not None, "validate_golden_dataset is missing"

    assert validator(_dataset(), require_reviewed=True) == []
