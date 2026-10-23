"""Deterministic evaluation metrics engine and threshold gate."""

from __future__ import annotations

import math
from collections import Counter
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
    total_path_length = 0
    total_matched = 0

    for r in results:
        if r.case_id not in cases:
            continue
        expected = [(h.source, h.target) for h in cases[r.case_id].expected_handoffs]
        actual = [(h.source, h.target) for h in r.actual_handoffs]
        # Ordered LCS counts missing, unexpected and out-of-order transitions.
        # Normalize by the longer path, with one slot for an empty/empty path.
        rows = len(expected) + 1
        columns = len(actual) + 1
        lcs = [[0] * columns for _ in range(rows)]
        for expected_index, expected_edge in enumerate(expected, start=1):
            for actual_index, actual_edge in enumerate(actual, start=1):
                if expected_edge == actual_edge:
                    lcs[expected_index][actual_index] = (
                        lcs[expected_index - 1][actual_index - 1] + 1
                    )
                else:
                    lcs[expected_index][actual_index] = max(
                        lcs[expected_index - 1][actual_index],
                        lcs[expected_index][actual_index - 1],
                    )
        total_path_length += max(1, len(expected), len(actual))
        total_matched += lcs[-1][-1]

    return (
        _round_dec(total_matched / total_path_length)
        if total_path_length > 0
        else Decimal("0.0000")
    )


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
    """Score only decisions for the reviewed target write tools.

    Case-level risk labels describe the intended operation, not an accidentally
    selected tool. Excluding off-target decisions keeps routing errors in the
    routing/tool metrics instead of attributing them to Policy.
    """
    cases = _case_map(dataset)
    matches = 0
    evaluated = 0

    for r in results:
        if r.case_id not in cases:
            continue
        expected = cases[r.case_id]
        expected_tools = set(expected.expected_tools)
        observations = [
            observation
            for observation in r.policy_decisions
            if observation.tool_name in expected_tools
        ]
        if (
            not observations
            and r.predicted_risk is not None
            and r.actual_require_human is not None
        ):
            # Compatibility for unit-level metric callers that provide one
            # measured decision in the legacy scalar fields.
            # Legacy scalar observations have no tool attribution and cannot
            # support a defensible operation-level Policy score.
            observations = []
        for observation in observations:
            evaluated += 1
            is_expected_high = expected.expected_require_human
            expected_risk = (
                expected.expected_risk.value
                if observation.tool_name == "process_refund" or is_expected_high
                else {
                    "create_ticket": "low",
                    "request_access": "medium",
                    "reset_password": "medium",
                }.get(observation.tool_name, expected.expected_risk.value)
            )
            allowed_matches = observation.allowed == (
                str(expected.expected_outcome.value).lower() != "escalated"
            )
            if (
                str(observation.risk_level).lower() == str(expected_risk).lower()
                and observation.require_human == is_expected_high
                and allowed_matches
            ):
                matches += 1

    return _round_dec(matches / evaluated) if evaluated > 0 else Decimal("0.0000")


def compute_policy_decision_coverage(
    results: Sequence[CaseExecutionResult], dataset: GoldenDataset
) -> Decimal:
    """Fraction of cases with an expected write tool that produced a Policy observation."""
    cases = _case_map(dataset)
    expected = {
        case.case_id
        for case in dataset.cases
        if set(case.expected_tools).intersection(
            {"create_ticket", "request_access", "reset_password", "process_refund"}
        )
    }
    if not expected:
        return Decimal("1.0000")
    observed = {
        result.case_id
        for result in results
        if result.case_id in expected
        and any(
            observation.tool_name in set(cases[result.case_id].expected_tools)
            for observation in result.policy_decisions
        )
    }
    return _round_dec(len(observed) / len(expected))


def count_policy_decisions_evaluated(
    results: Sequence[CaseExecutionResult], dataset: GoldenDataset
) -> int:
    cases = _case_map(dataset)
    return sum(
        1
        for result in results
        if result.case_id in cases
        for observation in result.policy_decisions
        if observation.tool_name in set(cases[result.case_id].expected_tools)
    )


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
            and Counter(r.actual_tools) == Counter(expected.expected_tools)
            and r.error_code is None
            and not r.high_risk_bypass
            and not any(outcome == "failed_final" for outcome in r.tool_outcomes)
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
        if r.actual_business_steps is None:
            continue
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
        actual = r.actual_tools
        total_calls += len(actual)
        seen: set[str] = set()
        for tool in actual:
            if tool in seen:
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
        policy_decision_coverage=compute_policy_decision_coverage(results, dataset),
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
        policy_decisions_evaluated=count_policy_decisions_evaluated(results, dataset),
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
            passed=(
                metrics.policy_decisions_evaluated > 0
                and metrics.policy_decision_accuracy == Decimal("1.0000")
            ),
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
    return ThresholdGateResult(
        passed=all_passed,
        checks=checks,
        details={
            "trajectory_threshold_source": "measured_baseline"
            if baseline is not None
            else "design_gate",
            "redundant_tool_threshold_source": "measured_baseline"
            if baseline is not None
            else "design_gate",
            "latency_threshold_source": "measured_baseline"
            if baseline is not None
            else "design_gate",
            "policy_decisions_evaluated": metrics.policy_decisions_evaluated,
            "policy_decision_coverage": float(metrics.policy_decision_coverage),
        },
    )
