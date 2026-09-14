from __future__ import annotations

import importlib
from decimal import Decimal

import pytest
from pydantic import ValidationError


def _agents_schema(name: str) -> type:
    module = importlib.import_module("agents.schemas")
    value = getattr(module, name, None)
    assert value is not None, f"{name} contract is missing"
    return value


def _tools_schema(name: str) -> type:
    module = importlib.import_module("tools.schemas")
    value = getattr(module, name, None)
    assert value is not None, f"{name} contract is missing"
    return value


def test_intent_and_ticket_status_are_closed_sets() -> None:
    intent = _agents_schema("Intent")
    ticket_status = _agents_schema("TicketStatus")

    assert {item.value for item in intent} == {
        "network",
        "account",
        "permission",
        "query",
        "high_risk",
    }
    assert {item.value for item in ticket_status} == {
        "queued",
        "running",
        "waiting_approval",
        "resolved",
        "escalated",
        "rejected",
        "failed",
        "expired",
    }


def test_low_confidence_route_must_escalate() -> None:
    routing_decision = _agents_schema("RoutingDecision")

    with pytest.raises(ValidationError, match="low-confidence"):
        routing_decision.model_validate(
            {
                "kind": "route",
                "intent": "query",
                "next_agent": "knowledge",
                "confidence": 0.69,
                "escalate": False,
                "reason_code": "needs_knowledge",
            }
        )


def test_pending_operation_rejects_model_supplied_unknown_fields() -> None:
    pending_operation = _agents_schema("PendingOperation")

    with pytest.raises(ValidationError, match="approved"):
        pending_operation.model_validate(
            {
                "operation_id": "op-001",
                "tool_name": "reset_password",
                "validated_args": {
                    "kind": "reset_password",
                    "user_id": "user-001",
                    "account_version": 3,
                },
                "args_hash": "a" * 64,
                "side_effects": "write",
                "evidence_result_ids": ["result-001"],
                "proposed_by": "action",
                "proposed_at": "2026-09-14T08:00:00Z",
                "schema_version": "1.0.0",
                "approved": True,
            }
        )


def test_pending_operation_requires_matching_tool_and_arguments() -> None:
    pending_operation = _agents_schema("PendingOperation")

    with pytest.raises(ValidationError, match="must match"):
        pending_operation.model_validate(
            {
                "operation_id": "op-001",
                "tool_name": "reset_password",
                "validated_args": {
                    "kind": "process_refund",
                    "requester_id": "user-001",
                    "order_id": "ORD-1001",
                    "amount": "50.00",
                    "currency": "USD",
                    "order_version": 2,
                },
                "args_hash": "a" * 64,
                "side_effects": "write",
                "evidence_result_ids": ["result-001"],
                "proposed_by": "action",
                "proposed_at": "2026-09-14T08:00:00Z",
                "schema_version": "1.0.0",
            }
        )


def test_pending_operation_rejects_naive_datetime() -> None:
    pending_operation = _agents_schema("PendingOperation")

    with pytest.raises(ValidationError, match="timezone"):
        pending_operation.model_validate(
            {
                "operation_id": "op-001",
                "tool_name": "reset_password",
                "validated_args": {
                    "kind": "reset_password",
                    "user_id": "user-001",
                    "account_version": 3,
                },
                "args_hash": "a" * 64,
                "side_effects": "write",
                "evidence_result_ids": ["result-001"],
                "proposed_by": "action",
                "proposed_at": "2026-09-14T08:00:00",
                "schema_version": "1.0.0",
            }
        )


@pytest.mark.parametrize("amount", ["0", "-1.00", "10.001"])
def test_refund_amount_rejects_non_positive_or_fractional_cents(amount: str) -> None:
    refund_args = _tools_schema("ProcessRefundArgs")

    with pytest.raises(ValidationError):
        refund_args.model_validate(
            {
                "kind": "process_refund",
                "requester_id": "user-001",
                "order_id": "ORD-1001",
                "amount": amount,
                "currency": "USD",
                "order_version": 2,
            }
        )


def test_refund_amount_remains_decimal() -> None:
    refund_args = _tools_schema("ProcessRefundArgs")

    parsed = refund_args.model_validate(
        {
            "kind": "process_refund",
            "requester_id": "user-001",
            "order_id": "ORD-1001",
            "amount": "199.99",
            "currency": "USD",
            "order_version": 2,
        }
    )

    assert parsed.amount == Decimal("199.99")
    assert isinstance(parsed.amount, Decimal)


def test_high_risk_decision_always_requires_human() -> None:
    risk_decision = _agents_schema("RiskDecision")

    with pytest.raises(ValidationError, match="high-risk"):
        risk_decision.model_validate(
            {
                "allowed": True,
                "risk_level": "high",
                "require_human": False,
                "reason_code": "requires_refund_approval",
                "explanation": "Refund reached the approval threshold.",
            }
        )


def test_tool_argument_models_reject_unknown_fields() -> None:
    query_user_args = _tools_schema("QueryUserArgs")

    with pytest.raises(ValidationError, match="unexpected"):
        query_user_args.model_validate(
            {
                "kind": "query_user",
                "user_id": "user-001",
                "unexpected": True,
            }
        )
