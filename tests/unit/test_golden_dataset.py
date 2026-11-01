from __future__ import annotations

import importlib
from collections import Counter
from pathlib import Path

from tools.registry import TOOL_REGISTRY


def _dataset_module():
    return importlib.import_module("scripts.maintenance.validate_golden_dataset")


def _dataset():
    loader = getattr(_dataset_module(), "load_golden_dataset", None)
    assert loader is not None, "load_golden_dataset is missing"
    return loader()


def test_golden_dataset_is_a_reviewed_full_dataset_with_unique_cases() -> None:
    dataset = _dataset()

    assert dataset.version == "1.0.0"
    assert dataset.level == "full"
    assert dataset.review_status == "reviewed"
    assert len(dataset.cases) == 80
    assert len({case.case_id for case in dataset.cases}) == 80


def test_each_intent_has_sixteen_cases() -> None:
    distribution = Counter(case.expected_intent.value for case in _dataset().cases)

    assert distribution == {
        "network": 16,
        "account": 16,
        "permission": 16,
        "query": 16,
        "high_risk": 16,
    }


def test_smoke_dataset_is_validated_by_band_instead_of_fixed_count() -> None:
    validator = getattr(_dataset_module(), "validate_golden_dataset", None)
    loader = getattr(_dataset_module(), "load_golden_dataset", None)
    assert validator is not None and loader is not None

    path = Path(__file__).resolve().parents[2] / "resources/evaluation/smoke_dataset.json"
    smoke = loader(path)

    assert smoke.level == "smoke"
    assert 20 <= len(smoke.cases) <= 79
    assert validator(smoke, require_reviewed=True) == []

    mislabelled = smoke.model_copy(update={"level": "full"})
    issues = validator(mislabelled, require_reviewed=True)
    assert any("at least 80 cases" in issue for issue in issues)


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
