"""Unit tests for deterministic policy risk matrix and authorization rules."""

from __future__ import annotations

from decimal import Decimal

from agents.action import create_pending_operation
from agents.policy import (
    REFUND_APPROVAL_THRESHOLD_USD,
    assess_risk,
)
from agents.schemas import (
    ActionProposal,
    ActorContext,
    PendingOperation,
    PolicyReason,
    ResourceContext,
    RiskLevel,
)
from tools.schemas import (
    CreateTicketArgs,
    ProcessRefundArgs,
    RequestAccessArgs,
    ResetPasswordArgs,
)

AUTH_REQUESTER = ActorContext(
    user_id="usr-alice",
    role="requester",
    authenticated=True,
)
UNAUTH_REQUESTER = ActorContext(
    user_id="usr-anon",
    role="requester",
    authenticated=False,
)


def make_pending(tool_name: str, args: object) -> PendingOperation:
    proposal = ActionProposal(
        tool_name=tool_name,  # type: ignore[arg-type]
        arguments=args,  # type: ignore[arg-type]
        evidence_result_ids=[],
    )
    return create_pending_operation(proposal)


# ---------------------------------------------------------------------------
# Read Tools
# ---------------------------------------------------------------------------


def test_policy_allows_read_tools_as_low_risk() -> None:
    decision = assess_risk("query_user", actor=AUTH_REQUESTER)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.LOW
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_READ


def test_policy_denies_unknown_tools() -> None:
    decision = assess_risk("delete_database", actor=AUTH_REQUESTER)
    assert decision.allowed is False
    assert decision.risk_level is RiskLevel.LOW
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.DENY_UNKNOWN_TOOL


def test_policy_denies_unauthenticated_actor() -> None:
    op = make_pending("create_ticket", CreateTicketArgs(
        requester_id="usr-anon",
        title="Help",
        description="Issue",
        source_ticket_id="t-1",
    ))
    decision = assess_risk(op, actor=UNAUTH_REQUESTER)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_UNAUTHORIZED


# ---------------------------------------------------------------------------
# create_ticket (Low risk write)
# ---------------------------------------------------------------------------


def test_create_ticket_for_self_is_low_risk() -> None:
    op = make_pending("create_ticket", CreateTicketArgs(
        requester_id="usr-alice",
        title="Follow-up investigation",
        description="Check network configuration",
        source_ticket_id="t-1",
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.LOW
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_SELF_SERVICE


def test_create_ticket_for_other_user_is_denied() -> None:
    op = make_pending("create_ticket", CreateTicketArgs(
        requester_id="usr-bob",
        title="Follow-up",
        description="Other user issue",
        source_ticket_id="t-1",
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


# ---------------------------------------------------------------------------
# request_access (Medium risk write, create application only)
# ---------------------------------------------------------------------------


def test_request_access_for_self_is_medium_risk() -> None:
    op = make_pending("request_access", RequestAccessArgs(
        requester_id="usr-alice",
        resource="repo:agentdesk",
        justification="Engineering work",
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.MEDIUM
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_SELF_SERVICE


def test_privileged_self_access_request_requires_human_approval() -> None:
    op = make_pending(
        "request_access",
        RequestAccessArgs(
            requester_id="usr-alice",
            resource="核心账务系统管理员访问权限",
            justification="临时工需要管理后台权限",
        ),
    )

    decision = assess_risk(op, actor=AUTH_REQUESTER)

    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.require_human is True
    assert decision.reason_code is PolicyReason.REQUIRES_PRIVILEGED_ACCESS_APPROVAL


def test_request_access_for_other_user_is_denied() -> None:
    op = make_pending("request_access", RequestAccessArgs(
        requester_id="usr-bob",
        resource="repo:agentdesk",
        justification="Engineering work",
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


# ---------------------------------------------------------------------------
# reset_password (Medium risk, self + active only)
# ---------------------------------------------------------------------------


def test_reset_password_for_active_self_is_medium_risk() -> None:
    op = make_pending("reset_password", ResetPasswordArgs(
        user_id="usr-alice",
        account_version=1,
    ))
    resource = ResourceContext(
        resource_type="user",
        resource_id="usr-alice",
        owner_id="usr-alice",
        status="active",
        version=1,
    )
    decision = assess_risk(op, actor=AUTH_REQUESTER, resource=resource)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.MEDIUM
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_SELF_SERVICE


def test_reset_password_for_inactive_account_is_denied() -> None:
    op = make_pending("reset_password", ResetPasswordArgs(
        user_id="usr-alice",
        account_version=1,
    ))
    resource = ResourceContext(
        resource_type="user",
        resource_id="usr-alice",
        owner_id="usr-alice",
        status="inactive",
        version=1,
    )
    decision = assess_risk(op, actor=AUTH_REQUESTER, resource=resource)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_INACTIVE_ACCOUNT


def test_reset_password_for_other_user_is_denied() -> None:
    op = make_pending("reset_password", ResetPasswordArgs(
        user_id="usr-bob",
        account_version=1,
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


# ---------------------------------------------------------------------------
# process_refund (199.99 vs 200.00 threshold)
# ---------------------------------------------------------------------------


def test_refund_below_threshold_is_medium_risk_and_automated() -> None:
    amount = Decimal("199.99")
    assert amount < REFUND_APPROVAL_THRESHOLD_USD

    op = make_pending("process_refund", ProcessRefundArgs(
        requester_id="usr-alice",
        order_id="ord-1",
        amount=amount,
        currency="USD",
        order_version=1,
    ))
    resource = ResourceContext(
        resource_type="order",
        resource_id="ord-1",
        owner_id="usr-alice",
        status="paid",
        version=1,
        refundable_amount=Decimal("300.00"),
    )
    decision = assess_risk(op, actor=AUTH_REQUESTER, resource=resource)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.MEDIUM
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_LOW_REFUND


def test_refund_at_threshold_is_high_risk_and_requires_human() -> None:
    amount = Decimal("200.00")
    assert amount == REFUND_APPROVAL_THRESHOLD_USD

    op = make_pending("process_refund", ProcessRefundArgs(
        requester_id="usr-alice",
        order_id="ord-1",
        amount=amount,
        currency="USD",
        order_version=1,
    ))
    resource = ResourceContext(
        resource_type="order",
        resource_id="ord-1",
        owner_id="usr-alice",
        status="paid",
        version=1,
        refundable_amount=Decimal("300.00"),
    )
    decision = assess_risk(op, actor=AUTH_REQUESTER, resource=resource)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.require_human is True
    assert decision.reason_code is PolicyReason.REQUIRES_REFUND_APPROVAL


def test_refund_above_threshold_is_high_risk_and_requires_human() -> None:
    amount = Decimal("250.00")
    assert amount > REFUND_APPROVAL_THRESHOLD_USD

    op = make_pending("process_refund", ProcessRefundArgs(
        requester_id="usr-alice",
        order_id="ord-1",
        amount=amount,
        currency="USD",
        order_version=1,
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.require_human is True
    assert decision.reason_code is PolicyReason.REQUIRES_REFUND_APPROVAL


def test_refund_for_non_owner_order_is_denied() -> None:
    op = make_pending("process_refund", ProcessRefundArgs(
        requester_id="usr-bob",
        order_id="ord-1",
        amount=Decimal("50.00"),
        currency="USD",
        order_version=1,
    ))
    decision = assess_risk(op, actor=AUTH_REQUESTER)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


def test_refund_for_mismatched_resource_owner_is_denied() -> None:
    op = make_pending("process_refund", ProcessRefundArgs(
        requester_id="usr-alice",
        order_id="ord-1",
        amount=Decimal("50.00"),
        currency="USD",
        order_version=1,
    ))
    resource = ResourceContext(
        resource_type="order",
        resource_id="ord-1",
        owner_id="usr-charlie",
        status="paid",
        version=1,
    )
    decision = assess_risk(op, actor=AUTH_REQUESTER, resource=resource)
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER
