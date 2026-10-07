"""Canonical Agent and workflow state contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias, TypedDict

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tools.schemas import (
    ActionToolName,
    ToolArgsUnion,
    ToolResultUnion,
    WriteToolName,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intent(StrEnum):
    NETWORK = "network"
    ACCOUNT = "account"
    PERMISSION = "permission"
    QUERY = "query"
    HIGH_RISK = "high_risk"


class TicketStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ResolutionMode(StrEnum):
    AUTOMATED = "automated"
    FALLBACK = "fallback"
    HUMAN_REQUIRED = "human_required"


class ToolOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CACHE_HIT = "cache_hit"


class RoutingReason(StrEnum):
    NEEDS_KNOWLEDGE = "needs_knowledge"
    NEEDS_ACTION = "needs_action"
    COMPLETE = "complete"
    LOW_CONFIDENCE = "low_confidence"


class PolicyReason(StrEnum):
    ALLOW_READ = "allow_read"
    ALLOW_SELF_SERVICE = "allow_self_service"
    ALLOW_LOW_REFUND = "allow_low_refund"
    REQUIRES_REFUND_APPROVAL = "requires_refund_approval"
    DENY_NOT_OWNER = "deny_not_owner"
    DENY_INACTIVE_ACCOUNT = "deny_inactive_account"
    DENY_UNKNOWN_TOOL = "deny_unknown_tool"
    DENY_UNAUTHORIZED = "deny_unauthorized"


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
HashDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class RoutingDecision(StrictModel):
    kind: Literal["route"] = "route"
    intent: Intent
    next_agent: Literal["knowledge", "action", "finalize"]
    confidence: float = Field(ge=0.0, le=1.0)
    escalate: bool
    reason_code: RoutingReason

    @model_validator(mode="after")
    def require_escalation_for_low_confidence(self) -> Self:
        if self.confidence < 0.7 and not self.escalate:
            raise ValueError("low-confidence routes must escalate")
        return self


class FinalResponse(StrictModel):
    kind: Literal["final"] = "final"
    resolution_mode: ResolutionMode
    final_response: Annotated[str, Field(min_length=1, max_length=4_000)]


SupervisorOutput: TypeAlias = Annotated[
    RoutingDecision | FinalResponse,
    Field(discriminator="kind"),
]


class KnowledgeRef(StrictModel):
    document_id: Identifier
    chunk_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content_excerpt: Annotated[str, Field(min_length=1, max_length=1_000)]
    score: float = Field(ge=0.0, le=1.0)


class KnowledgeAgentOutput(StrictModel):
    answer: Annotated[str, Field(min_length=1, max_length=4_000)]
    knowledge_refs: list[KnowledgeRef]
    fallback: bool = False


class ActionProposal(StrictModel):
    tool_name: ActionToolName
    arguments: ToolArgsUnion
    evidence_result_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_matching_argument_kind(self) -> Self:
        if self.tool_name != self.arguments.kind:
            raise ValueError("tool_name must match arguments.kind")
        return self


class PendingOperation(StrictModel):
    operation_id: Identifier
    tool_name: WriteToolName
    validated_args: ToolArgsUnion
    args_hash: HashDigest
    side_effects: Literal["write"]
    evidence_result_ids: list[Identifier]
    proposed_by: Literal["action"]
    proposed_at: AwareDatetime
    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]

    @model_validator(mode="after")
    def require_matching_argument_kind(self) -> Self:
        if self.tool_name != self.validated_args.kind:
            raise ValueError("tool_name must match validated_args.kind")
        return self


class RiskDecision(StrictModel):
    allowed: bool
    risk_level: RiskLevel
    require_human: bool
    reason_code: PolicyReason
    explanation: Annotated[str, Field(min_length=1, max_length=1_000)]

    @model_validator(mode="after")
    def require_human_for_high_risk(self) -> Self:
        if self.risk_level is RiskLevel.HIGH and not self.require_human:
            raise ValueError("high-risk decisions must require human approval")
        return self


class PolicyExplanation(StrictModel):
    explanation: Annotated[str, Field(min_length=1, max_length=1_000)]


class ApprovalSnapshot(StrictModel):
    approval_id: Identifier
    operation_id: Identifier
    args_hash: HashDigest
    status: ApprovalStatus
    version: int = Field(ge=1)
    decided_by: Identifier | None = None
    decided_at: AwareDatetime | None = None
    expires_at: AwareDatetime


class ToolResult(StrictModel):
    request_id: Identifier
    operation_id: Identifier | None
    # Open by design (architecture §6.6): a rejected attempt must be able to name
    # a tool that is not in the registry, otherwise F-04 could not be audited.
    tool_name: Identifier
    outcome: ToolOutcome
    data: ToolResultUnion | None
    error_code: Annotated[str, Field(min_length=1, max_length=128)] | None
    retry_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class AgentStep(StrictModel):
    step: int = Field(ge=1)
    actor: Literal["supervisor", "knowledge", "action", "policy", "approval", "dispatcher"]
    action: Annotated[str, Field(min_length=1, max_length=200)]
    timestamp: AwareDatetime


class ActorContext(StrictModel):
    user_id: Identifier
    role: Literal["requester", "approver"]
    authenticated: bool


class ResourceContext(StrictModel):
    resource_type: Literal["user", "order", "access_request", "ticket"]
    resource_id: Identifier
    owner_id: Identifier
    status: Annotated[str, Field(min_length=1, max_length=64)]
    version: int = Field(ge=1)
    refundable_amount: Annotated[
        Decimal,
        Field(ge=Decimal("0"), max_digits=12, decimal_places=2),
    ] | None = None


class TicketState(TypedDict):
    ticket_id: str
    thread_id: str
    requester_id: str
    user_query: str
    trace_id: str
    status: TicketStatus
    intent: Intent | None
    routing_decision: RoutingDecision | None
    knowledge_refs: list[KnowledgeRef]
    action_proposal: ActionProposal | None
    pending_operation: PendingOperation | None
    tool_results: list[ToolResult]
    risk_decision: RiskDecision | None
    approval: ApprovalSnapshot | None
    agent_history: list[AgentStep]
    step_count: int
    resolution_mode: ResolutionMode | None
    final_response: str | None
