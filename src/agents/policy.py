"""Deterministic policy decisions and risk matrix for AgentDesk."""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

from agents.schemas import (
    ActorContext,
    PendingOperation,
    PolicyReason,
    ResourceContext,
    RiskDecision,
    RiskLevel,
)
from tools.schemas import (
    ActionToolName,
    CreateTicketArgs,
    ProcessRefundArgs,
    RequestAccessArgs,
    ResetPasswordArgs,
    WriteToolName,
)

REFUND_APPROVAL_THRESHOLD_USD = Decimal("200.00")

PHASE1_READ_TOOLS: frozenset[str] = frozenset(
    {"search_knowledge", "query_user", "query_order", "check_service"}
)

PHASE1_ACTION_READ_TOOLS: tuple[ActionToolName, ...] = (
    "query_user",
    "query_order",
    "check_service",
)

WRITE_TOOL_NAMES: frozenset[str] = frozenset(get_args(WriteToolName))


class WriteCapabilityDisabled(RuntimeError):
    """Raised when any write capability is requested in the read-only phase."""


def assess_phase1_read(tool_name: str) -> RiskDecision:
    """Approve an enabled read capability and refuse everything else."""
    if tool_name not in PHASE1_READ_TOOLS:
        raise WriteCapabilityDisabled(
            f"{tool_name!r} is not an enabled read capability in phase 1"
        )
    return RiskDecision(
        allowed=True,
        risk_level=RiskLevel.LOW,
        require_human=False,
        reason_code=PolicyReason.ALLOW_READ,
        explanation=(
            f"{tool_name} reads data only, produces no external side effect and "
            "requires no human approval."
        ),
    )


def assess_risk(
    operation: PendingOperation | str,
    *,
    actor: ActorContext,
    resource: ResourceContext | None = None,
) -> RiskDecision:
    """Pure function evaluating the deterministic risk matrix.

    Invariants (architecture §7.4):
    - Final risk decision is strictly deterministic; LLM never decides allow/deny.
    - High risk always requires human approval (require_human=True).
    - Threshold boundary: refund >= 200.00 USD is high risk; < 200.00 USD is medium.
    - Cross-user operations and unknown tools are denied.
    """
    if not actor.authenticated:
        return RiskDecision(
            allowed=False,
            risk_level=RiskLevel.LOW,
            require_human=False,
            reason_code=PolicyReason.DENY_UNAUTHORIZED,
            explanation=f"Actor {actor.user_id} is not authenticated.",
        )

    if isinstance(operation, str):
        if operation in PHASE1_READ_TOOLS:
            return RiskDecision(
                allowed=True,
                risk_level=RiskLevel.LOW,
                require_human=False,
                reason_code=PolicyReason.ALLOW_READ,
                explanation=(
                    f"{operation} reads data only, produces no external side effect "
                    "and requires no human approval."
                ),
            )
        return RiskDecision(
            allowed=False,
            risk_level=RiskLevel.LOW,
            require_human=False,
            reason_code=PolicyReason.DENY_UNKNOWN_TOOL,
            explanation=f"Tool {operation!r} is unrecognized or not permitted.",
        )

    tool_name = operation.tool_name
    args = operation.validated_args

    if tool_name not in WRITE_TOOL_NAMES:
        return RiskDecision(
            allowed=False,
            risk_level=RiskLevel.LOW,
            require_human=False,
            reason_code=PolicyReason.DENY_UNKNOWN_TOOL,
            explanation=f"Write tool {tool_name!r} is not in the allowed registry.",
        )

    if isinstance(args, CreateTicketArgs):
        if args.requester_id != actor.user_id:
            return RiskDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                require_human=False,
                reason_code=PolicyReason.DENY_NOT_OWNER,
                explanation=(
                    f"Requester {actor.user_id} cannot create ticket for another "
                    f"user {args.requester_id}."
                ),
            )
        return RiskDecision(
            allowed=True,
            risk_level=RiskLevel.LOW,
            require_human=False,
            reason_code=PolicyReason.ALLOW_SELF_SERVICE,
            explanation="Ticket creation is self-service and low risk.",
        )

    if isinstance(args, RequestAccessArgs):
        if args.requester_id != actor.user_id:
            return RiskDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                require_human=False,
                reason_code=PolicyReason.DENY_NOT_OWNER,
                explanation=(
                    f"Requester {actor.user_id} cannot request access on behalf of "
                    f"{args.requester_id}."
                ),
            )
        return RiskDecision(
            allowed=True,
            risk_level=RiskLevel.MEDIUM,
            require_human=False,
            reason_code=PolicyReason.ALLOW_SELF_SERVICE,
            explanation=(
                "Access request is self-service and creates a pending request only "
                "without granting direct permissions."
            ),
        )

    if isinstance(args, ResetPasswordArgs):
        if args.user_id != actor.user_id:
            return RiskDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                require_human=False,
                reason_code=PolicyReason.DENY_NOT_OWNER,
                explanation=(
                    f"Requester {actor.user_id} cannot reset password for {args.user_id}."
                ),
            )
        if resource is not None:
            if resource.owner_id != actor.user_id:
                return RiskDecision(
                    allowed=False,
                    risk_level=RiskLevel.LOW,
                    require_human=False,
                    reason_code=PolicyReason.DENY_NOT_OWNER,
                    explanation=(
                        f"Resource owner {resource.owner_id} does not match actor {actor.user_id}."
                    ),
                )
            if resource.status != "active":
                return RiskDecision(
                    allowed=False,
                    risk_level=RiskLevel.LOW,
                    require_human=False,
                    reason_code=PolicyReason.DENY_INACTIVE_ACCOUNT,
                    explanation=(
                        f"Cannot reset password for inactive account (status: {resource.status})."
                    ),
                )
        return RiskDecision(
            allowed=True,
            risk_level=RiskLevel.MEDIUM,
            require_human=False,
            reason_code=PolicyReason.ALLOW_SELF_SERVICE,
            explanation="Password reset for active self-service account is medium risk.",
        )

    if isinstance(args, ProcessRefundArgs):
        if args.requester_id != actor.user_id:
            return RiskDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                require_human=False,
                reason_code=PolicyReason.DENY_NOT_OWNER,
                explanation=(
                    f"Requester {actor.user_id} does not match "
                    f"refund requester {args.requester_id}."
                ),
            )
        if resource is not None and resource.owner_id != actor.user_id:
            return RiskDecision(
                allowed=False,
                risk_level=RiskLevel.LOW,
                require_human=False,
                reason_code=PolicyReason.DENY_NOT_OWNER,
                explanation=(
                    f"Order {args.order_id} owner {resource.owner_id} "
                    f"does not match actor {actor.user_id}."
                ),
            )
        if args.amount >= REFUND_APPROVAL_THRESHOLD_USD:
            return RiskDecision(
                allowed=True,
                risk_level=RiskLevel.HIGH,
                require_human=True,
                reason_code=PolicyReason.REQUIRES_REFUND_APPROVAL,
                explanation=(
                    f"Refund of {args.amount} USD meets or exceeds approval "
                    f"threshold {REFUND_APPROVAL_THRESHOLD_USD} USD."
                ),
            )
        return RiskDecision(
            allowed=True,
            risk_level=RiskLevel.MEDIUM,
            require_human=False,
            reason_code=PolicyReason.ALLOW_LOW_REFUND,
            explanation=(
                f"Refund of {args.amount} USD is below approval "
                f"threshold {REFUND_APPROVAL_THRESHOLD_USD} USD."
            ),
        )

    return RiskDecision(
        allowed=False,
        risk_level=RiskLevel.LOW,
        require_human=False,
        reason_code=PolicyReason.DENY_UNKNOWN_TOOL,
        explanation="Unrecognized operation arguments.",
    )
