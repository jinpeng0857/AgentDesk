"""Public API request and response contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from agents.schemas import ApprovalStatus, Intent, RiskLevel, TicketStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Message = Annotated[str, Field(min_length=1, max_length=1_000)]


class ErrorDetail(ApiModel):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]+$")]
    message: Message
    request_id: Identifier


class ErrorEnvelope(ApiModel):
    error: ErrorDetail


class LoginRequest(ApiModel):
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: SecretStr


class TokenResponse(ApiModel):
    access_token: Annotated[str, Field(min_length=1)]
    token_type: Literal["bearer"] = "bearer"
    expires_in_s: int = Field(gt=0)


class ActorResponse(ApiModel):
    user_id: Identifier
    role: Literal["requester", "approver"]


class TicketCreateRequest(ApiModel):
    user_query: Annotated[str, Field(min_length=1, max_length=2_000)]


class TicketAcceptedResponse(ApiModel):
    ticket_id: Identifier
    trace_id: Identifier
    status: Literal[TicketStatus.QUEUED]


class TicketSummaryResponse(ApiModel):
    ticket_id: Identifier
    status: TicketStatus
    intent: Intent | None
    risk_level: RiskLevel | None
    updated_at: AwareDatetime


class KnowledgeRefResponse(ApiModel):
    document_id: Identifier
    chunk_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content_excerpt: Annotated[str, Field(min_length=1, max_length=1_000)]
    score: float = Field(ge=0.0, le=1.0)


class TicketDetailResponse(TicketSummaryResponse):
    requester_id: Identifier
    resolution_mode: Literal["automated", "fallback", "human_required"] | None
    final_response: Annotated[str, Field(max_length=4_000)] | None
    knowledge_refs: list[KnowledgeRefResponse] = Field(default_factory=list)


class TraceEventResponse(ApiModel):
    request_id: Identifier
    ticket_id: Identifier
    trace_id: Identifier
    actor: Literal["supervisor", "knowledge", "action", "policy", "approval", "dispatcher"]
    event_type: Annotated[str, Field(min_length=1, max_length=128)]
    summary: Annotated[str, Field(min_length=1, max_length=1_000)]
    timestamp: AwareDatetime


class RedactedArgument(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    value: Annotated[str, Field(min_length=1, max_length=500)]


class ApprovalSummaryResponse(ApiModel):
    approval_id: Identifier
    ticket_id: Identifier
    operation_id: Identifier
    tool_name: Annotated[str, Field(min_length=1, max_length=128)]
    risk_level: RiskLevel
    status: ApprovalStatus
    version: int = Field(ge=1)
    created_at: AwareDatetime
    expires_at: AwareDatetime


class ApprovalDetailResponse(ApprovalSummaryResponse):
    args_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    args_summary: list[RedactedArgument]
    reason_code: Annotated[str, Field(min_length=1, max_length=128)]


class ApprovalDecisionRequest(ApiModel):
    decision: Literal["approve", "reject"]
    version: int = Field(ge=1)
    reason: Annotated[str, Field(min_length=1, max_length=1_000)] | None = None


class ApprovalDecisionResponse(ApiModel):
    approval_id: Identifier
    status: Literal["approved", "rejected"]
    version: int = Field(ge=2)


class EvalMetricResponse(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    value: Decimal
    unit: Annotated[str, Field(min_length=1, max_length=32)]


class EvalRunSummaryResponse(ApiModel):
    run_id: Identifier
    mode: Literal["offline", "live"]
    dataset_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    git_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    created_at: AwareDatetime


class EvalRunDetailResponse(EvalRunSummaryResponse):
    model: Annotated[str, Field(min_length=1, max_length=200)]
    prompt_versions: dict[str, Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]]
    metrics: list[EvalMetricResponse]


class HealthResponse(ApiModel):
    status: Literal["ok", "not_ready"]
