"""Typed tool input and output contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    LOCKED = "locked"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class ServiceStatus(StrEnum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"


class WriteResultStatus(StrEnum):
    CREATED = "created"
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"


ToolName: TypeAlias = Literal[
    "search_knowledge",
    "query_user",
    "query_order",
    "check_service",
    "create_ticket",
    "request_access",
    "reset_password",
    "process_refund",
]
ActionToolName: TypeAlias = Literal[
    "query_user",
    "query_order",
    "check_service",
    "create_ticket",
    "request_access",
    "reset_password",
    "process_refund",
]
WriteToolName: TypeAlias = Literal[
    "create_ticket",
    "request_access",
    "reset_password",
    "process_refund",
]

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=2_000)]
Money = Annotated[
    Decimal,
    Field(gt=Decimal("0"), max_digits=12, decimal_places=2),
]


class KnowledgeSearchItem(StrictModel):
    document_id: Identifier
    chunk_id: Identifier
    title: ShortText
    content_excerpt: Annotated[str, Field(min_length=1, max_length=1_000)]
    score: float = Field(ge=0.0, le=1.0)


class SearchKnowledgeArgs(StrictModel):
    kind: Literal["search_knowledge"] = "search_knowledge"
    query: Annotated[str, Field(min_length=1, max_length=500)]
    top_k: int = Field(default=5, ge=1, le=10)


class SearchKnowledgeResult(StrictModel):
    kind: Literal["search_knowledge"] = "search_knowledge"
    refs: list[KnowledgeSearchItem]


class QueryUserArgs(StrictModel):
    kind: Literal["query_user"] = "query_user"
    user_id: Identifier


class QueryUserResult(StrictModel):
    kind: Literal["query_user"] = "query_user"
    user_id: Identifier
    account_status: AccountStatus
    masked_email: Annotated[str, Field(min_length=3, max_length=254)]
    version: int = Field(ge=1)


class QueryOrderArgs(StrictModel):
    kind: Literal["query_order"] = "query_order"
    order_id: Identifier


class QueryOrderResult(StrictModel):
    kind: Literal["query_order"] = "query_order"
    order_id: Identifier
    requester_id: Identifier
    status: OrderStatus
    refundable_amount: Annotated[
        Decimal,
        Field(ge=Decimal("0"), max_digits=12, decimal_places=2),
    ]
    currency: Literal["USD"]
    version: int = Field(ge=1)


class CheckServiceArgs(StrictModel):
    kind: Literal["check_service"] = "check_service"
    service_name: ShortText


class CheckServiceResult(StrictModel):
    kind: Literal["check_service"] = "check_service"
    service_name: ShortText
    status: ServiceStatus
    incident_id: Identifier | None = None
    checked_at: AwareDatetime


class CreateTicketArgs(StrictModel):
    kind: Literal["create_ticket"] = "create_ticket"
    requester_id: Identifier
    title: ShortText
    description: LongText
    source_ticket_id: Identifier


class CreateTicketResult(StrictModel):
    kind: Literal["create_ticket"] = "create_ticket"
    downstream_ticket_id: Identifier
    status: WriteResultStatus


class RequestAccessArgs(StrictModel):
    kind: Literal["request_access"] = "request_access"
    requester_id: Identifier
    resource: ShortText
    justification: LongText


class RequestAccessResult(StrictModel):
    kind: Literal["request_access"] = "request_access"
    access_request_id: Identifier
    status: WriteResultStatus


class ResetPasswordArgs(StrictModel):
    kind: Literal["reset_password"] = "reset_password"
    user_id: Identifier
    account_version: int = Field(ge=1)


class ResetPasswordResult(StrictModel):
    kind: Literal["reset_password"] = "reset_password"
    user_id: Identifier
    temporary_password: SecretStr
    credential_version: int = Field(ge=1)


class ProcessRefundArgs(StrictModel):
    kind: Literal["process_refund"] = "process_refund"
    requester_id: Identifier
    order_id: Identifier
    amount: Money
    currency: Literal["USD"]
    order_version: int = Field(ge=1)


class ProcessRefundResult(StrictModel):
    kind: Literal["process_refund"] = "process_refund"
    refund_id: Identifier
    status: WriteResultStatus
    amount: Money
    currency: Literal["USD"]


ToolArgsUnion: TypeAlias = Annotated[
    SearchKnowledgeArgs
    | QueryUserArgs
    | QueryOrderArgs
    | CheckServiceArgs
    | CreateTicketArgs
    | RequestAccessArgs
    | ResetPasswordArgs
    | ProcessRefundArgs,
    Field(discriminator="kind"),
]

ToolResultUnion: TypeAlias = Annotated[
    SearchKnowledgeResult
    | QueryUserResult
    | QueryOrderResult
    | CheckServiceResult
    | CreateTicketResult
    | RequestAccessResult
    | ResetPasswordResult
    | ProcessRefundResult,
    Field(discriminator="kind"),
]
