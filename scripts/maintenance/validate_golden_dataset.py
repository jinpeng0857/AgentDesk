"""Validate the reviewed smoke evaluation dataset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from agents.schemas import Intent
from eval.schemas import GoldenDataset
from tools.registry import TOOL_REGISTRY

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = REPOSITORY_ROOT / "resources" / "evaluation" / "golden_dataset.json"
# AGENTS.md §7.1 levels the dataset by size: <20 is a development sample, 20-79 is
# a smoke evaluation and >=80 is a full evaluation. The validator enforces the
# band the declared level claims instead of a single fixed shape, and it keeps
# per-intent coverage so a smoke set cannot silently drop a routing class.
LEVEL_CASE_BANDS: dict[str, tuple[int, int | None]] = {
    "development": (1, 19),
    "smoke": (20, 79),
    "full": (80, None),
}
PREFIX_INTENT = {
    "NET": "network",
    "ACC": "account",
    "PRM": "permission",
    "QRY": "query",
    "RFD": "high_risk",
}


def load_golden_dataset(path: Path = DEFAULT_DATASET_PATH) -> GoldenDataset:
    return GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))


def validate_golden_dataset(
    dataset: GoldenDataset,
    *,
    require_reviewed: bool,
) -> list[str]:
    issues: list[str] = []
    if require_reviewed and dataset.review_status != "reviewed":
        issues.append("dataset requires human review")

    case_count = len(dataset.cases)
    low, high = LEVEL_CASE_BANDS[dataset.level]
    if case_count < low:
        issues.append(
            f"{dataset.level} dataset must contain at least {low} cases; got {case_count}"
        )
    if high is not None and case_count > high:
        issues.append(
            f"{dataset.level} dataset must contain at most {high} cases; got {case_count}"
        )

    case_ids = [case.case_id for case in dataset.cases]
    if len(set(case_ids)) != len(case_ids):
        issues.append("case_id values must be unique")

    missing_intents = sorted(set(Intent) - {case.expected_intent for case in dataset.cases})
    if missing_intents:
        issues.append(
            "dataset must cover every intent; missing "
            f"{[intent.value for intent in missing_intents]}"
        )
    for prefix, intent in sorted(PREFIX_INTENT.items()):
        for case in dataset.cases:
            if case.case_id.startswith(prefix + "-") and case.expected_intent.value != intent:
                issues.append(
                    f"{case.case_id} prefix {prefix} must declare intent {intent}"
                )

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
