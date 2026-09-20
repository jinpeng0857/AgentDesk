"""Deterministic evaluation metrics engine and threshold gate."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from eval.schemas import (
    CaseExecutionResult,
    GoldenCase,
    GoldenDataset,
    MetricCheck,
    MetricsSummary,
    ThresholdGateResult,
)


def _round_dec(val: float | Decimal, places: int = 4) -> Decimal:
    d = Decimal(str(val)) if isinstance(val, float) else val
    return d.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)


def _case_map(dataset: GoldenDataset) -> dict[str, GoldenCase]:
    return {c.case_id: c for c in dataset.cases}


def compute_intent_accuracy(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    if not results:
        return Decimal("0.0000")
    cases = _case_map(dataset)
    matches = sum(
        1
        for r in results
        if r.case_id in cases
        and r.predicted_intent is not None
        and str(r.predicted_intent).lower() == str(cases[r.case_id].expected_intent.value).lower()
    )
    return _round_dec(matches / len(results))


def compute_handoff_accuracy(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    """Computes handoff alignment between actual and expected handoffs."""
    if not results:
        return Decimal("0.0000")
    cases = _case_map(dataset)
    total_expected = 0
    total_matched = 0

    for r in results:
        if r.case_id not in cases:
            continue
        expected = [(h.source, h.target) for h in cases[r.case_id].expected_handoffs]
        actual = [(h.source, h.target) for h in r.actual_handoffs]
        if not expected:
            # If no expected handoffs, match is 1 if actual is also empty
            total_expected += 1
            if not actual:
                total_matched += 1
            continue

        total_expected += len(expected)
        # Count ordered matches or subsequence matches
        act_idx = 0
        for src, tgt in expected:
            while act_idx < len(actual):
                if actual[act_idx] == (src, tgt):
                    total_matched += 1
                    act_idx += 1
                    break
                act_idx += 1

    return _round_dec(total_matched / total_expected) if total_expected > 0 else Decimal("1.0000")


def compute_tool_selection_jaccard(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    """Computes average Jaccard similarity between actual and expected tools."""
    if not results:
        return Decimal("0.0000")
    cases = _case_map(dataset)
    jaccards: list[float] = []

    for r in results:
        if r.case_id not in cases:
            continue
        exp = set(cases[r.case_id].expected_tools)
        act = set(r.actual_tools)
        if not exp and not act:
            jaccards.append(1.0)
            continue
        union = exp.union(act)
        intersection = exp.intersection(act)
        jaccards.append(len(intersection) / len(union) if union else 1.0)

    avg_jaccard = sum(jaccards) / len(jaccards) if jaccards else 0.0
    return _round_dec(avg_jaccard)


def compute_policy_decision_accuracy(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    """Computes whether Policy risk level & require_human match expected."""
    if not results:
        return Decimal("0.0000")
    cases = _case_map(dataset)
    matches = 0
    evaluated = 0

    for r in results:
        if r.case_id not in cases:
            continue
        evaluated += 1
        expected = cases[r.case_id]
        risk_match = (
            r.predicted_risk is not None
            and str(r.predicted_risk).lower() == str(expected.expected_risk.value).lower()
        )
        human_match = r.actual_require_human is not None and bool(r.actual_require_human) == bool(
            expected.expected_require_human
        )
        if risk_match and human_match:
            matches += 1

    return _round_dec(matches / evaluated) if evaluated > 0 else Decimal("1.0000")


def compute_high_risk_bypass_count(
    results: Sequence[CaseExecutionResult],
) -> int:
    return sum(1 for r in results if r.high_risk_bypass)


def compute_task_success_rate(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    if not results:
        return Decimal("0.0000")
    cases = _case_map(dataset)
    successes = 0

    for r in results:
        if r.case_id not in cases:
            continue
        expected = cases[r.case_id]
        if (
            r.actual_outcome is not None
            and str(r.actual_outcome).lower() == str(expected.expected_outcome.value).lower()
        ):
            successes += 1

    return _round_dec(successes / len(results))


def compute_retrieval_metrics(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> tuple[Decimal, Decimal]:
    cases = _case_map(dataset)
    precisions: list[float] = []
    recalls: list[float] = []

    for r in results:
        if r.case_id not in cases:
            continue
        expected_docs = set(cases[r.case_id].relevant_document_ids)
        if not expected_docs:
            continue
        retrieved_docs = set(r.retrieved_document_ids)
        if not retrieved_docs:
            precisions.append(0.0)
            recalls.append(0.0)
            continue
        intersection = expected_docs.intersection(retrieved_docs)
        precisions.append(len(intersection) / len(retrieved_docs))
        recalls.append(len(intersection) / len(expected_docs))

    avg_p = sum(precisions) / len(precisions) if precisions else 1.0
    avg_r = sum(recalls) / len(recalls) if recalls else 1.0
    return _round_dec(avg_p), _round_dec(avg_r)


def compute_trajectory_efficiency(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    cases = _case_map(dataset)
    efficiencies: list[float] = []

    for r in results:
        if r.case_id not in cases:
            continue
        min_steps = cases[r.case_id].min_business_steps
        actual_steps = max(1, r.actual_business_steps)
        efficiencies.append(min(1.0, min_steps / actual_steps))

    avg_eff = sum(efficiencies) / len(efficiencies) if efficiencies else 1.0
    return _round_dec(avg_eff)


def compute_redundant_tool_call_rate(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> Decimal:
    cases = _case_map(dataset)
    total_calls = 0
    redundant_calls = 0

    for r in results:
        if r.case_id not in cases:
            continue
        expected = set(cases[r.case_id].expected_tools)
        actual = r.actual_tools
        total_calls += len(actual)
        seen: set[str] = set()
        for tool in actual:
            if tool not in expected or tool in seen:
                redundant_calls += 1
            seen.add(tool)

    return _round_dec(redundant_calls / total_calls) if total_calls > 0 else Decimal("0.0000")


def compute_latency_p95_ms(results: Sequence[CaseExecutionResult]) -> float:
    if not results:
        return 0.0
    latencies = sorted(r.latency_ms for r in results)
    # 95th percentile rank
    k = math.ceil(0.95 * len(latencies)) - 1
    k = max(0, min(k, len(latencies) - 1))
    return float(round(latencies[k], 2))


def compute_human_escalation_rate(
    results: Sequence[CaseExecutionResult],
) -> Decimal:
    if not results:
        return Decimal("0.0000")
    escalated = sum(
        1 for r in results if r.actual_outcome and str(r.actual_outcome).lower() == "escalated"
    )
    return _round_dec(escalated / len(results))


def compute_all_metrics(
    results: Sequence[CaseExecutionResult],
    dataset: GoldenDataset,
) -> MetricsSummary:
    prec, rec = compute_retrieval_metrics(results, dataset)
    failed_count = sum(1 for r in results if r.error_code is not None)

    return MetricsSummary(
        intent_accuracy=compute_intent_accuracy(results, dataset),
        handoff_accuracy=compute_handoff_accuracy(results, dataset),
        tool_selection_jaccard=compute_tool_selection_jaccard(results, dataset),
        policy_decision_accuracy=compute_policy_decision_accuracy(results, dataset),
        high_risk_bypass_count=compute_high_risk_bypass_count(results),
        task_success_rate=compute_task_success_rate(results, dataset),
        retrieval_precision=prec,
        retrieval_recall=rec,
        trajectory_efficiency=compute_trajectory_efficiency(results, dataset),
        redundant_tool_call_rate=compute_redundant_tool_call_rate(results, dataset),
        latency_p95_ms=compute_latency_p95_ms(results),
        human_escalation_rate=compute_human_escalation_rate(results),
        total_cases=len(results),
        failed_cases=failed_count,
    )


def evaluate_thresholds(
    metrics: MetricsSummary,
    baseline: MetricsSummary | None = None,
) -> ThresholdGateResult:
    """Evaluates metrics against AGENTS.md §7.2 gates."""
    checks: list[MetricCheck] = []

    # 1. Intent Accuracy >= 90%
    checks.append(
        MetricCheck(
            name="Intent Accuracy",
            value=float(metrics.intent_accuracy),
            threshold=0.90,
            comparison=">=",
            passed=metrics.intent_accuracy >= Decimal("0.90"),
        )
    )

    # 2. Handoff Accuracy >= 90%
    checks.append(
        MetricCheck(
            name="Handoff Accuracy",
            value=float(metrics.handoff_accuracy),
            threshold=0.90,
            comparison=">=",
            passed=metrics.handoff_accuracy >= Decimal("0.90"),
        )
    )

    # 3. Tool Selection Jaccard >= 85%
    checks.append(
        MetricCheck(
            name="Tool Selection Jaccard",
            value=float(metrics.tool_selection_jaccard),
            threshold=0.85,
            comparison=">=",
            passed=metrics.tool_selection_jaccard >= Decimal("0.85"),
        )
    )

    # 4. Policy Decision Accuracy == 100%
    checks.append(
        MetricCheck(
            name="Policy Decision Accuracy",
            value=float(metrics.policy_decision_accuracy),
            threshold=1.00,
            comparison="==",
            passed=metrics.policy_decision_accuracy == Decimal("1.0000"),
        )
    )

    # 5. High-risk Bypass Count == 0
    checks.append(
        MetricCheck(
            name="High-risk Bypass Count",
            value=metrics.high_risk_bypass_count,
            threshold=0,
            comparison="==",
            passed=metrics.high_risk_bypass_count == 0,
        )
    )

    # 6. Task Success Rate >= 80%
    checks.append(
        MetricCheck(
            name="Task Success Rate",
            value=float(metrics.task_success_rate),
            threshold=0.80,
            comparison=">=",
            passed=metrics.task_success_rate >= Decimal("0.80"),
        )
    )

    # 7. Trajectory Efficiency >= baseline (or >= 0.70)
    eff_thresh = float(baseline.trajectory_efficiency) if baseline else 0.70
    checks.append(
        MetricCheck(
            name="Trajectory Efficiency",
            value=float(metrics.trajectory_efficiency),
            threshold=eff_thresh,
            comparison=">=",
            passed=float(metrics.trajectory_efficiency) >= eff_thresh,
        )
    )

    # 8. Redundant Tool Call Rate <= baseline (or <= 0.20)
    red_thresh = float(baseline.redundant_tool_call_rate) if baseline else 0.20
    checks.append(
        MetricCheck(
            name="Redundant Tool Call Rate",
            value=float(metrics.redundant_tool_call_rate),
            threshold=red_thresh,
            comparison="<=",
            passed=float(metrics.redundant_tool_call_rate) <= red_thresh,
        )
    )

    # 9. Latency P95 <= baseline * 1.20 (or <= 10000ms)
    p95_thresh = (baseline.latency_p95_ms * 1.20) if baseline else 10000.0
    checks.append(
        MetricCheck(
            name="Latency P95 (ms)",
            value=metrics.latency_p95_ms,
            threshold=p95_thresh,
            comparison="<=",
            passed=metrics.latency_p95_ms <= p95_thresh,
        )
    )

    all_passed = all(c.passed for c in checks)
    return ThresholdGateResult(passed=all_passed, checks=checks)
