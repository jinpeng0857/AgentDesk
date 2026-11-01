"""Diagnose live E2E eval failures from a candidate artifact.

Classifies per-case failures across outcome, handoff divergence point, tool set
mismatch, retrieval, and escalation cause. Read-only analysis: it never changes
an artifact or the dataset.

Usage:
    uv run python scripts/maintenance/diagnose_eval_failures.py <artifact.json>
    uv run python scripts/maintenance/diagnose_eval_failures.py <artifact.json> --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]
CSV_COLUMNS = (
    "case_id",
    "query",
    "expected_intent",
    "predicted_intent",
    "expected_handoffs",
    "actual_handoffs",
    "handoff_exact",
    "handoff_lcs_ratio",
    "first_divergence",
    "expected_tools",
    "actual_tools",
    "tools_exact",
    "expected_outcome",
    "actual_outcome",
    "outcome_match",
    "expected_relevant_document_ids",
    "retrieved_document_ids",
    "retrieved_references_count",
    "latency_ms",
    "error_code",
    "tool_error_codes",
    "failure_bucket",
)


def load(path: Path) -> list[JsonObject]:
    with path.open(encoding="utf-8") as handle:
        data: JsonObject = json.load(handle)
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise ValueError("artifact has no cases list")
    return [case for case in cases if isinstance(case, dict)]


def handoff_key(handoff: JsonObject) -> str:
    return f"{handoff['source']}->{handoff['target']}"


def first_divergence(
    expected: list[JsonObject], actual: list[JsonObject]
) -> tuple[int, str, str]:
    for index, (expected_edge, actual_edge) in enumerate(
        zip(expected, actual, strict=False)
    ):
        if expected_edge != actual_edge:
            return index, handoff_key(expected_edge), handoff_key(actual_edge)
    if len(expected) > len(actual):
        return len(actual), handoff_key(expected[len(actual)]), "<missing>"
    if len(actual) > len(expected):
        return len(expected), "<extra>", handoff_key(actual[len(expected)])
    return -1, "", ""


def ordered_lcs(left: list[str], right: list[str]) -> int:
    rows = len(left) + 1
    columns = len(right) + 1
    table = [[0] * columns for _ in range(rows)]
    for i in range(1, rows):
        for j in range(1, columns):
            if left[i - 1] == right[j - 1]:
                table[i][j] = table[i - 1][j - 1] + 1
            else:
                table[i][j] = max(table[i - 1][j], table[i][j - 1])
    return table[-1][-1]


def classify(case: JsonObject) -> str:
    """Assign one dominant failure bucket using artifact evidence only."""
    expected_handoffs = [handoff_key(h) for h in case["expected_handoffs"]]
    actual_handoffs = [handoff_key(h) for h in case["actual_handoffs"]]
    matched = ordered_lcs(expected_handoffs, actual_handoffs)
    denominator = max(1, len(expected_handoffs), len(actual_handoffs))
    handoff_exact = (
        matched == denominator and len(expected_handoffs) == len(actual_handoffs)
    )
    tools_exact = sorted(case["actual_tools"]) == sorted(case["expected_tools"])
    outcome_match = case["actual_outcome"] == case["expected_outcome"]
    if handoff_exact and tools_exact and outcome_match and not case["error_code"]:
        return "ok"
    if case["error_code"]:
        return "model-output-invalid"
    if case["expected_outcome"] == "rejected" and case["actual_outcome"] != "rejected":
        return "expected-rejection-not-reached"
    if (
        case["actual_tools"]
        and any(code == "POLICY_DENIED" for code in case["tool_error_codes"])
    ):
        return "policy-denied-escalated"
    if any(code == "TOOL_EXECUTION_FAILED" for code in case["tool_error_codes"]):
        return "tool-execution-failed"
    if "knowledge" in case["observed_graph_nodes"] and not case["retrieved_document_ids"]:
        return "knowledge-refs-empty"
    if case["expected_intent"] == "high_risk" and not case["actual_tools"]:
        return "high-risk-finalized-without-action"
    expected_targets = {h["target"] for h in case["expected_handoffs"]}
    actual_targets = {h["target"] for h in case["actual_handoffs"]}
    if "action_propose" in expected_targets and "knowledge" in actual_targets:
        return "record-query-routed-to-knowledge"
    if (
        "finalize" in expected_targets
        and "action_propose" in actual_targets
        and "action_propose" not in expected_targets
    ):
        return "extra-step-after-completion"
    return "other"


def to_row(case: JsonObject) -> dict[str, object]:
    expected_handoffs = [handoff_key(h) for h in case["expected_handoffs"]]
    actual_handoffs = [handoff_key(h) for h in case["actual_handoffs"]]
    matched = ordered_lcs(expected_handoffs, actual_handoffs)
    denominator = max(1, len(expected_handoffs), len(actual_handoffs))
    _index, expected_edge, actual_edge = first_divergence(
        case["expected_handoffs"], case["actual_handoffs"]
    )
    return {
        "case_id": case["case_id"],
        "query": case.get("query", ""),
        "expected_intent": case["expected_intent"],
        "predicted_intent": case.get("predicted_intent"),
        "expected_handoffs": " | ".join(expected_handoffs),
        "actual_handoffs": " | ".join(actual_handoffs),
        "handoff_exact": (
            matched == denominator and len(expected_handoffs) == len(actual_handoffs)
        ),
        "handoff_lcs_ratio": round(matched / denominator, 4),
        "first_divergence": (
            f"{expected_edge} => {actual_edge}" if expected_edge else ""
        ),
        "expected_tools": ",".join(case["expected_tools"]),
        "actual_tools": ",".join(case["actual_tools"]),
        "tools_exact": sorted(case["actual_tools"]) == sorted(case["expected_tools"]),
        "expected_outcome": case["expected_outcome"],
        "actual_outcome": case["actual_outcome"],
        "outcome_match": case["actual_outcome"] == case["expected_outcome"],
        "expected_relevant_document_ids": ",".join(case["expected_relevant_document_ids"]),
        "retrieved_document_ids": ",".join(case["retrieved_document_ids"]),
        "retrieved_references_count": len(case.get("retrieved_references", [])),
        "latency_ms": round(float(case["latency_ms"]), 2),
        "error_code": case.get("error_code") or "",
        "tool_error_codes": ",".join(case.get("tool_error_codes") or []),
        "failure_bucket": classify(case),
    }


def write_csv(cases: list[JsonObject], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for case in cases:
            writer.writerow(to_row(case))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()

    cases = load(args.artifact)
    if args.csv is not None:
        write_csv(cases, args.csv)
        print(f"wrote {len(cases)} rows to {args.csv}")

    print(f"total={len(cases)}")
    print("\n=== 1. Terminal confusion matrix (expected -> actual) ===")
    outcomes = Counter(
        (case["expected_outcome"], case["actual_outcome"]) for case in cases
    )
    for (expected, actual), count in outcomes.most_common():
        print(f"  {count:3d}x {expected} -> {actual}")

    print("\n=== 2. Handoff: first divergence ===")
    divergence: Counter[tuple[str, str]] = Counter()
    divergence_cases: dict[tuple[str, str], list[str]] = {}
    for case in cases:
        expected_handoffs = case["expected_handoffs"]
        actual_handoffs = case["actual_handoffs"]
        if actual_handoffs == expected_handoffs:
            continue
        _index, expected_edge, actual_edge = first_divergence(
            expected_handoffs, actual_handoffs
        )
        key = (expected_edge, actual_edge)
        divergence[key] += 1
        divergence_cases.setdefault(key, []).append(case["case_id"])
    for (expected_edge, actual_edge), count in divergence.most_common(40):
        print(f"  {count:3d}x expected {expected_edge} | actual {actual_edge}")
        print(
            f"        cases: {', '.join(divergence_cases[(expected_edge, actual_edge)][:12])}"
        )

    print("\n=== 3. Failure buckets ===")
    buckets = Counter(classify(case) for case in cases)
    for bucket, count in buckets.most_common():
        print(f"  {count:3d}x {bucket}")

    print("\n=== 4. Tool set: missing / extra ===")
    missing: Counter[str] = Counter()
    extra: Counter[str] = Counter()
    for case in cases:
        expected_tools = set(case["expected_tools"])
        actual_tools = set(case["actual_tools"])
        for tool in expected_tools - actual_tools:
            missing[tool] += 1
        for tool in actual_tools - expected_tools:
            extra[tool] += 1
    print("  missing (expected but absent):", dict(missing.most_common()))
    print("  extra (present but unexpected):", dict(extra.most_common()))

    print("\n=== 5. Retrieval ===")
    needs_retrieval = [c for c in cases if c["expected_relevant_document_ids"]]
    got_references = [c for c in cases if c["retrieved_document_ids"]]
    visited_knowledge = [c for c in cases if "knowledge" in c["observed_graph_nodes"]]
    print(f"  cases needing documents: {len(needs_retrieval)}")
    print(f"  cases with retrieved_document_ids: {len(got_references)}")
    print(f"  cases that visited knowledge: {len(visited_knowledge)}")

    print("\n=== 6. Latency ===")
    latencies = sorted(float(case["latency_ms"]) for case in cases)
    print(
        f"  min={latencies[0]:.0f} p50={statistics.median(latencies):.0f} "
        f"p95={latencies[max(0, int(0.95 * len(latencies)) - 1)]:.0f} "
        f"max={latencies[-1]:.0f}"
    )


if __name__ == "__main__":
    main()
