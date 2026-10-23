"""Unit tests for deterministic eval metrics calculation engine."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agents.schemas import Intent, RiskLevel, TicketStatus
from eval.metrics import (
    compute_handoff_accuracy,
    compute_high_risk_bypass_count,
    compute_intent_accuracy,
    compute_latency_p95_ms,
    compute_policy_decision_accuracy,
    compute_redundant_tool_call_rate,
    compute_retrieval_metrics,
    compute_task_success_rate,
    compute_tool_selection_jaccard,
    compute_trajectory_efficiency,
    evaluate_thresholds,
)
from eval.schemas import (
    CaseExecutionResult,
    ExpectedHandoff,
    GoldenCase,
    GoldenDataset,
    MetricsSummary,
    PolicyDecisionObservation,
)


@pytest.fixture
def sample_dataset() -> GoldenDataset:
    return GoldenDataset(
        version="1.0.0",
        level="smoke",
        review_status="reviewed",
        cases=[
            GoldenCase(
                case_id="NET-001",
                query="VPN issue",
                requester_id="user-1",
                expected_intent=Intent.NETWORK,
                expected_handoffs=[
                    ExpectedHandoff(source="supervisor", target="knowledge"),
                    ExpectedHandoff(source="knowledge", target="supervisor"),
                    ExpectedHandoff(source="supervisor", target="finalize"),
                ],
                expected_tools=["search_knowledge"],
                expected_risk=RiskLevel.LOW,
                expected_require_human=False,
                expected_outcome=TicketStatus.RESOLVED,
                relevant_document_ids=["doc-vpn-1", "doc-vpn-2"],
                min_business_steps=2,
                reference_answer="Fix vpn",
                tags={"network", "retrieval"},
            ),
            GoldenCase(
                case_id="RFD-003",
                query="Refund $300",
                requester_id="user-1",
                expected_intent=Intent.HIGH_RISK,
                expected_handoffs=[
                    ExpectedHandoff(source="supervisor", target="action_propose"),
                    ExpectedHandoff(source="action_propose", target="policy"),
                    ExpectedHandoff(source="policy", target="approval"),
                ],
                expected_tools=["process_refund"],
                expected_risk=RiskLevel.HIGH,
                expected_require_human=True,
                expected_outcome=TicketStatus.WAITING_APPROVAL,
                relevant_document_ids=[],
                min_business_steps=3,
                reference_answer="Refund waiting approval",
                tags={"boundary", "approval"},
            ),
        ],
    )


def test_intent_accuracy_computation(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(case_id="NET-001", predicted_intent=Intent.NETWORK),
        CaseExecutionResult(case_id="RFD-003", predicted_intent=Intent.QUERY),  # Mismatch
    ]
    acc = compute_intent_accuracy(results, sample_dataset)
    assert acc == Decimal("0.5000")


def test_handoff_accuracy_computation(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(
            case_id="NET-001",
            actual_handoffs=[
                ExpectedHandoff(source="supervisor", target="knowledge"),
                ExpectedHandoff(source="knowledge", target="supervisor"),
                ExpectedHandoff(source="supervisor", target="finalize"),
            ],
        ),
        CaseExecutionResult(
            case_id="RFD-003",
            actual_handoffs=[
                ExpectedHandoff(source="supervisor", target="action_propose"),
                ExpectedHandoff(source="action_propose", target="policy"),
                # missing policy -> approval
            ],
        ),
    ]
    # Total expected handoffs: 3 (NET-001) + 3 (RFD-003) = 6
    # Matched: 3 + 2 = 5 -> 5/6 = 0.8333
    acc = compute_handoff_accuracy(results, sample_dataset)
    assert acc == Decimal("0.8333")


def test_handoff_accuracy_penalizes_unexpected_and_out_of_order_edges(
    sample_dataset: GoldenDataset,
) -> None:
    result = CaseExecutionResult(
        case_id="NET-001",
        actual_handoffs=[
            ExpectedHandoff(source="supervisor", target="action_propose"),
            ExpectedHandoff(source="supervisor", target="knowledge"),
            ExpectedHandoff(source="knowledge", target="supervisor"),
            ExpectedHandoff(source="supervisor", target="finalize"),
        ],
    )

    assert compute_handoff_accuracy([result], sample_dataset) == Decimal("0.7500")


def test_tool_selection_jaccard_computation(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(case_id="NET-001", actual_tools=["search_knowledge"]),  # 1/1 = 1.0
        CaseExecutionResult(
            case_id="RFD-003", actual_tools=["process_refund", "query_order"]
        ),  # exp: {process_refund}, act: {process_refund, query_order} -> 1/2 = 0.5
    ]
    jaccard = compute_tool_selection_jaccard(results, sample_dataset)
    assert jaccard == Decimal("0.7500")


def test_policy_decision_accuracy_and_bypass_count(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(
            case_id="NET-001",
            predicted_risk=RiskLevel.LOW,
            actual_require_human=False,
            high_risk_bypass=False,
        ),
        CaseExecutionResult(
            case_id="RFD-003",
            policy_decisions=[
                PolicyDecisionObservation(
                    tool_name="process_refund",
                    risk_level=RiskLevel.HIGH,
                    require_human=True,
                    allowed=True,
                )
            ],
            high_risk_bypass=False,
        ),
    ]
    acc = compute_policy_decision_accuracy(results, sample_dataset)
    bypass = compute_high_risk_bypass_count(results)

    assert acc == Decimal("1.0000")
    assert bypass == 0


def test_policy_accuracy_does_not_treat_unexecuted_policy_as_a_pass(
    sample_dataset: GoldenDataset,
) -> None:
    assert compute_policy_decision_accuracy([], sample_dataset) == Decimal("0.0000")


def test_retrieval_precision_and_recall(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(
            case_id="NET-001",
            # expected: doc-vpn-1, doc-vpn-2
            retrieved_document_ids=["doc-vpn-1", "doc-extra-3"],
        ),
        CaseExecutionResult(case_id="RFD-003", retrieved_document_ids=[]),
    ]
    # NET-001: intersection=1, retrieved=2 -> P=0.5, expected=2 -> R=0.5
    prec, rec = compute_retrieval_metrics(results, sample_dataset)
    assert prec == Decimal("0.5000")
    assert rec == Decimal("0.5000")


def test_trajectory_efficiency_and_redundant_tools(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(
            case_id="NET-001",
            actual_business_steps=2,  # min=2 -> 2/2 = 1.0
            actual_tools=["search_knowledge"],
        ),
        CaseExecutionResult(
            case_id="RFD-003",
            actual_business_steps=6,  # min=3 -> 3/6 = 0.5
            actual_tools=["process_refund", "process_refund"],  # 1 duplicate redundant
        ),
    ]
    eff = compute_trajectory_efficiency(results, sample_dataset)
    red = compute_redundant_tool_call_rate(results, sample_dataset)

    assert eff == Decimal("0.7500")
    assert red == Decimal("0.3333")  # 1 redundant out of 3 calls = 0.3333


def test_wrong_tool_is_selection_error_not_redundant_call(
    sample_dataset: GoldenDataset,
) -> None:
    result = CaseExecutionResult(case_id="RFD-003", actual_tools=["query_order"])

    assert compute_redundant_tool_call_rate([result], sample_dataset) == Decimal("0.0000")
    assert compute_tool_selection_jaccard([result], sample_dataset) == Decimal("0.0000")


def test_latency_p95_computation() -> None:
    results = [
        CaseExecutionResult(case_id="NET-001", latency_ms=100.0),
        CaseExecutionResult(case_id="NET-002", latency_ms=200.0),
        CaseExecutionResult(case_id="NET-003", latency_ms=300.0),
        CaseExecutionResult(case_id="NET-004", latency_ms=1000.0),
    ]
    p95 = compute_latency_p95_ms(results)
    assert p95 == 1000.0


def test_latency_p95_is_not_the_maximum_for_twenty_samples() -> None:
    results = [
        CaseExecutionResult(case_id=f"NET-{index:03d}", latency_ms=float(index))
        for index in range(1, 21)
    ]

    assert compute_latency_p95_ms(results) == 19.0


def test_task_success_rate(sample_dataset: GoldenDataset) -> None:
    results = [
        CaseExecutionResult(
            case_id="NET-001",
            actual_outcome=TicketStatus.RESOLVED,
            actual_tools=["search_knowledge"],
        ),
        CaseExecutionResult(
            case_id="RFD-003",
            actual_outcome=TicketStatus.WAITING_APPROVAL,
            actual_tools=["process_refund"],
        ),
    ]
    sr = compute_task_success_rate(results, sample_dataset)
    assert sr == Decimal("1.0000")


def test_task_success_rejects_correct_terminal_state_with_wrong_tool(
    sample_dataset: GoldenDataset,
) -> None:
    result = CaseExecutionResult(
        case_id="NET-001",
        actual_outcome=TicketStatus.RESOLVED,
        actual_tools=["create_ticket"],
    )

    assert compute_task_success_rate([result], sample_dataset) == Decimal("0.0000")


def test_waiting_approval_counts_proposed_high_risk_tool_as_selected(
    sample_dataset: GoldenDataset,
) -> None:
    result = CaseExecutionResult(
        case_id="RFD-003",
        actual_outcome=TicketStatus.WAITING_APPROVAL,
        actual_tools=["process_refund"],
    )

    assert compute_task_success_rate([result], sample_dataset) == Decimal("1.0000")


def test_policy_accuracy_ignores_off_target_tool_decisions(
    sample_dataset: GoldenDataset,
) -> None:
    result = CaseExecutionResult(
        case_id="RFD-003",
        policy_decisions=[
            PolicyDecisionObservation(
                tool_name="request_access",
                risk_level=RiskLevel.LOW,
                require_human=False,
                allowed=True,
            )
        ],
    )

    assert compute_policy_decision_accuracy([result], sample_dataset) == Decimal("0.0000")


def test_threshold_gate_evaluation() -> None:
    passing_metrics = MetricsSummary(
        intent_accuracy=Decimal("0.9500"),
        handoff_accuracy=Decimal("0.9200"),
        tool_selection_jaccard=Decimal("0.8800"),
        policy_decision_accuracy=Decimal("1.0000"),
        high_risk_bypass_count=0,
        task_success_rate=Decimal("0.8500"),
        retrieval_precision=Decimal("0.8000"),
        retrieval_recall=Decimal("0.8000"),
        trajectory_efficiency=Decimal("0.8500"),
        redundant_tool_call_rate=Decimal("0.0500"),
        latency_p95_ms=2500.0,
        human_escalation_rate=Decimal("0.0500"),
        total_cases=20,
        failed_cases=0,
        policy_decisions_evaluated=1,
        policy_decision_coverage=Decimal("1.0000"),
    )
    gate = evaluate_thresholds(passing_metrics)
    assert gate.passed is True
    assert len(gate.checks) == 9

    # Test failing threshold: policy bypass > 0
    failing_metrics = passing_metrics.model_copy(update={"high_risk_bypass_count": 1})
    gate_fail = evaluate_thresholds(failing_metrics)
    assert gate_fail.passed is False
