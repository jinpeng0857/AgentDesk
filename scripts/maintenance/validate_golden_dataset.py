"""Validate the reviewed smoke evaluation dataset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from eval.schemas import GoldenDataset
from tools.registry import TOOL_REGISTRY

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = REPOSITORY_ROOT / "resources" / "evaluation" / "golden_dataset.json"
EXPECTED_INTENT_DISTRIBUTION = {
    "network": 16,
    "account": 16,
    "permission": 16,
    "query": 16,
    "high_risk": 16,
}


def load_golden_dataset(path: Path = DEFAULT_DATASET_PATH) -> GoldenDataset:
    return GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))


def validate_golden_dataset(
    dataset: GoldenDataset,
    *,
    require_reviewed: bool,
) -> list[str]:
    issues: list[str] = []
    if dataset.version != "1.0.0":
        issues.append("dataset version must be 1.0.0")
    if dataset.level != "full":
        issues.append("dataset level must be full")
    if require_reviewed and dataset.review_status != "reviewed":
        issues.append("dataset requires human review")
    if len(dataset.cases) < 80:
        issues.append("full evaluation dataset must contain at least 80 cases")

    case_ids = [case.case_id for case in dataset.cases]
    if len(set(case_ids)) != len(case_ids):
        issues.append("case_id values must be unique")

    intent_distribution = Counter(case.expected_intent.value for case in dataset.cases)
    if dict(intent_distribution) != EXPECTED_INTENT_DISTRIBUTION:
        issues.append("each intent must have exactly 16 cases")

    write_tools = {
        name for name, spec in TOOL_REGISTRY.items() if spec.side_effects == "write"
    }
    for case in dataset.cases:
        unknown_tools = set(case.expected_tools).difference(TOOL_REGISTRY)
        if unknown_tools:
            issues.append(f"{case.case_id} references unknown tools: {sorted(unknown_tools)}")
        if case.expected_require_human and case.expected_risk.value != "high":
            issues.append(f"{case.case_id} human approval requires high risk")
        if (
            case.expected_outcome.value == "waiting_approval"
            and not case.expected_require_human
        ):
            issues.append(f"{case.case_id} waits for approval without requiring a human")

        nodes = {
            handoff.source for handoff in case.expected_handoffs
        } | {handoff.target for handoff in case.expected_handoffs}
        if write_tools.intersection(case.expected_tools) and "policy" not in nodes:
            issues.append(f"{case.case_id} write path does not include policy")
        if case.relevant_document_ids and "search_knowledge" not in case.expected_tools:
            issues.append(f"{case.case_id} has documents without search_knowledge")
    return sorted(issues)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-pending-review", action="store_true")
    parser.add_argument("--path", type=Path, default=DEFAULT_DATASET_PATH)
    args = parser.parse_args()

    dataset = load_golden_dataset(args.path)
    issues = validate_golden_dataset(
        dataset,
        require_reviewed=not args.allow_pending_review,
    )
    intent_distribution = Counter(case.expected_intent.value for case in dataset.cases)
    risk_distribution = Counter(case.expected_risk.value for case in dataset.cases)
    tool_coverage = sorted(
        {tool_name for case in dataset.cases for tool_name in case.expected_tools}
    )
    summary = {
        "case_count": len(dataset.cases),
        "intent_distribution": dict(sorted(intent_distribution.items())),
        "level": dataset.level,
        "review_status": dataset.review_status,
        "risk_distribution": dict(sorted(risk_distribution.items())),
        "tool_coverage": tool_coverage,
        "version": dataset.version,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if issues:
        for issue in issues:
            print(f"- {issue}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
