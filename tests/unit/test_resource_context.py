"""Policy and execution must evaluate a write operation against the same resource.

Regression coverage for the E2E failure where the Policy node approved a
self-service password reset (allowed=True) and the write gateway re-evaluated
the same operation with resource=None, producing POLICY_DENIED.
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.action import create_pending_operation
from agents.policy import assess_risk
from agents.schemas import ActionProposal, ActorContext, PolicyReason
from backend.graph.nodes import _resource_context_for_operation
from tools.schemas import ProcessRefundArgs, ResetPasswordArgs


class _StubSession:
    def __init__(self, records: dict[str, object]) -> None:
        self._records = records

    async def get(self, _model: object, key: str) -> object | None:
        return self._records.get(key)


def _reset_operation(user_id: str):
    return create_pending_operation(
        ActionProposal(
            tool_name="reset_password",
            arguments=ResetPasswordArgs(user_id=user_id, account_version=1),
            evidence_result_ids=[],
        )
    )


def _refund_operation(order_id: str):
    from decimal import Decimal

    return create_pending_operation(
        ActionProposal(
            tool_name="process_refund",
            arguments=ProcessRefundArgs(
                requester_id="user-requester-001",
                order_id=order_id,
                amount=Decimal("199.99"),
                currency="USD",
                order_version=1,
            ),
            evidence_result_ids=[],
        )
    )


async def test_reset_resource_context_reports_active_self_service_account() -> None:
    operation = _reset_operation("user-requester-001")
    session = _StubSession(
        {"user-requester-001": SimpleNamespace(id="user-requester-001", active=True, version=1)}
    )

    resource = await _resource_context_for_operation(session, operation)  # type: ignore[arg-type]

    assert resource is not None
    assert (resource.resource_type, resource.owner_id, resource.status) == (
        "user",
        "user-requester-001",
        "active",
    )
    actor = ActorContext(user_id="user-requester-001", role="requester", authenticated=True)
    decision = assess_risk(operation, actor=actor, resource=resource)
    assert decision.allowed is True
    assert decision.reason_code is PolicyReason.ALLOW_SELF_SERVICE


async def test_reset_resource_context_marks_missing_account_not_found() -> None:
    operation = _reset_operation("user-missing")

    resource = await _resource_context_for_operation(  # type: ignore[arg-type]
        _StubSession({}), operation
    )

    assert resource is not None
    assert resource.status == "not_found"


async def test_refund_resource_context_carries_order_owner_for_policy() -> None:
    from decimal import Decimal

    operation = _refund_operation("ORD-2001")
    session = _StubSession(
        {
            "ORD-2001": SimpleNamespace(
                id="ORD-2001",
                requester_id="user-requester-002",
                status="paid",
                version=1,
                refundable_amount=Decimal("75.00"),
            )
        }
    )

    resource = await _resource_context_for_operation(session, operation)  # type: ignore[arg-type]

    assert resource is not None
    assert (resource.resource_type, resource.owner_id, resource.status) == (
        "order",
        "user-requester-002",
        "paid",
    )
    actor = ActorContext(user_id="user-requester-001", role="requester", authenticated=True)
    decision = assess_risk(operation, actor=actor, resource=resource)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


async def test_refund_resource_context_handles_unknown_order() -> None:
    operation = _refund_operation("ORD-9999")

    resource = await _resource_context_for_operation(  # type: ignore[arg-type]
        _StubSession({}), operation
    )

    assert resource is not None
    assert (resource.resource_type, resource.status) == ("order", "not_found")
