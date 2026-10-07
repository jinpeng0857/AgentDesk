"""Closed Tool schema registry.

The registry deliberately contains specifications only. Executable callables are
resolved by the governance executor in a later phase.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from tools.schemas import (
    CheckServiceArgs,
    CheckServiceResult,
    CreateTicketArgs,
    CreateTicketResult,
    ProcessRefundArgs,
    ProcessRefundResult,
    QueryOrderArgs,
    QueryOrderResult,
    QueryUserArgs,
    QueryUserResult,
    RequestAccessArgs,
    RequestAccessResult,
    ResetPasswordArgs,
    ResetPasswordResult,
    SearchKnowledgeArgs,
    SearchKnowledgeResult,
    ToolName,
)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ToolName
    description: str = Field(min_length=1)
    parameters: dict[str, object]
    result_schema: dict[str, object]
    side_effects: Literal["read", "write"]
    risk_level: Literal["low", "medium", "high"]
    idempotent: bool
    timeout_s: float = Field(gt=0)


ToolModelPair = tuple[type[BaseModel], type[BaseModel]]

TOOL_MODEL_PAIRS: dict[ToolName, ToolModelPair] = {
    "search_knowledge": (SearchKnowledgeArgs, SearchKnowledgeResult),
    "query_user": (QueryUserArgs, QueryUserResult),
    "query_order": (QueryOrderArgs, QueryOrderResult),
    "check_service": (CheckServiceArgs, CheckServiceResult),
    "create_ticket": (CreateTicketArgs, CreateTicketResult),
    "request_access": (RequestAccessArgs, RequestAccessResult),
    "reset_password": (ResetPasswordArgs, ResetPasswordResult),
    "process_refund": (ProcessRefundArgs, ProcessRefundResult),
}


def _spec(
    *,
    name: ToolName,
    description: str,
    side_effects: Literal["read", "write"],
    risk_level: Literal["low", "medium", "high"],
    timeout_s: float,
) -> ToolSpec:
    input_model, result_model = TOOL_MODEL_PAIRS[name]
    return ToolSpec(
        name=name,
        description=description,
        parameters=cast(dict[str, object], input_model.model_json_schema()),
        result_schema=cast(dict[str, object], result_model.model_json_schema()),
        side_effects=side_effects,
        risk_level=risk_level,
        idempotent=True,
        timeout_s=timeout_s,
    )


TOOL_REGISTRY = MappingProxyType(
    {
        "search_knowledge": _spec(
            name="search_knowledge",
            description=(
                "Side effects: read. Required inputs: query and top_k. "
                "Result: ranked knowledge references with document and chunk identifiers. "
                "Sensitive data: none; indexed cases must already be redacted. Approval: none."
            ),
            side_effects="read",
            risk_level="low",
            timeout_s=8.0,
        ),
        "query_user": _spec(
            name="query_user",
            description=(
                "Side effects: read. Required inputs: user_id. "
                "Result: account status, masked email, and resource version. "
                "Sensitive data: masked email only. Approval: none."
            ),
            side_effects="read",
            risk_level="low",
            timeout_s=3.0,
        ),
        "query_order": _spec(
            name="query_order",
            description=(
                "Side effects: read. Required inputs: order_id. "
                "Result: owner, status, refundable amount, currency, and version. "
                "Sensitive data: requester identifier and order details must be access-controlled. "
                "Approval: none."
            ),
            side_effects="read",
            risk_level="low",
            timeout_s=3.0,
        ),
        "check_service": _spec(
            name="check_service",
            description=(
                "Side effects: read. Required inputs: service_name. "
                "Result: operational status, optional incident ID, and check time. "
                "Sensitive data: none. Approval: none."
            ),
            side_effects="read",
            risk_level="low",
            timeout_s=5.0,
        ),
        "create_ticket": _spec(
            name="create_ticket",
            description=(
                "Side effects: write. Required inputs: requester_id, title, description, "
                "and source_ticket_id. Result: downstream ticket ID and status. "
                "Sensitive data: description may contain user-provided details and must be "
                "redacted in audit. Approval: Policy is required; allowed low-risk requests need "
                "no human."
            ),
            side_effects="write",
            risk_level="low",
            timeout_s=5.0,
        ),
        "request_access": _spec(
            name="request_access",
            description=(
                "Side effects: write. Required inputs: requester_id, resource, and justification. "
                "Result: access request ID and status; this never grants access directly. "
                "Sensitive data: requester and justification must be redacted in audit. "
                "Approval: Policy is required; allowed self-requests need no human in this MVP."
            ),
            side_effects="write",
            risk_level="medium",
            timeout_s=5.0,
        ),
        "reset_password": _spec(
            name="reset_password",
            description=(
                "Side effects: write. Required inputs: user_id and account_version. "
                "Result: user ID, temporary password, and credential version. "
                "Sensitive data: temporary_password must be encrypted at rest and never logged. "
                "Approval: Policy is required; only an active self-owned account is allowed."
            ),
            side_effects="write",
            risk_level="medium",
            timeout_s=10.0,
        ),
        "process_refund": _spec(
            name="process_refund",
            description=(
                "Side effects: write. Required inputs: requester_id, order_id, amount, currency, "
                "and order_version. Result: refund ID, status, amount, and currency. "
                "Sensitive data: order and requester details must be redacted in audit. "
                "Approval: Policy is required; amount at or above 200.00 USD requires a human."
            ),
            side_effects="write",
            risk_level="medium",
            timeout_s=10.0,
        ),
    }
)

AGENT_TOOL_WHITELIST = {
    "supervisor": (),
    "knowledge": ("search_knowledge",),
    "action": (
        "query_user",
        "query_order",
        "check_service",
        "create_ticket",
        "request_access",
        "reset_password",
        "process_refund",
    ),
    "policy": (),
}
