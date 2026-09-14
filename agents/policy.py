"""Deterministic policy decisions for the phase 1 read-only slice.

This module deliberately implements only the read-capability gate. The full risk
matrix, refund threshold and approval rules belong to the write-operation phase
and must not be approximated here.
"""

from __future__ import annotations

from typing import get_args

from agents.schemas import PolicyReason, RiskDecision, RiskLevel
from tools.schemas import ActionToolName, WriteToolName

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
    """Approve an enabled read capability and refuse everything else.

    Refusing here — rather than in a tool implementation — is what makes the
    phase 1 zero-write guarantee a property of the gateway instead of a promise.
    """
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
