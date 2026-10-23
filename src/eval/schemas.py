"""Evaluation dataset and artifact contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from agents.schemas import Intent, RiskLevel, TicketStatus
from tools.schemas import ToolName


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExpectedHandoff(StrictModel):
    source: Annotated[str, Field(min_length=1, max_length=64, alias="from")]
    target: Annotated[str, Field(min_length=1, max_length=64, alias="to")]


class GoldenCase(StrictModel):
    case_id: Annotated[str, Field(pattern=r"^[A-Z]{3}-\d{3}$")]
    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    requester_id: Annotated[str, Field(min_length=1, max_length=128)]
    expected_intent: Intent
    expected_handoffs: list[ExpectedHandoff]
    expected_tools: list[ToolName]
    expected_risk: RiskLevel
    expected_require_human: bool
    expected_outcome: TicketStatus
    relevant_document_ids: list[Annotated[str, Field(min_length=1, max_length=128)]]
    min_business_steps: int = Field(ge=1)
    reference_answer: Annotated[str, Field(min_length=1, max_length=4_000)]
    tags: set[Annotated[str, Field(min_length=1, max_length=64)]]


class GoldenDataset(StrictModel):
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    level: Literal["development", "smoke", "full"]
    review_status: Literal["pending_review", "reviewed"]
    cases: list[GoldenCase]


class EvalArtifactMetadata(StrictModel):
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    mode: Literal["offline", "live"]
    dataset_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    base_url_id: Annotated[str, Field(min_length=1, max_length=128)]
    prompt_versions: dict[str, Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]]
    git_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    created_at: AwareDatetime
    cost_usd: Annotated[
        Decimal,
        Field(ge=Decimal("0"), max_digits=12, decimal_places=6),
    ]


class PolicyDecisionObservation(StrictModel):
    tool_name: ToolName
    risk_level: RiskLevel | str
    require_human: bool
    allowed: bool
    operation_id: str | None = None
    reason_code: str | None = None


class RoutingDecisionObservation(StrictModel):
    intent: Intent
    next_agent: Literal["knowledge", "action", "finalize"]
    confidence: float = Field(ge=0.0, le=1.0)
    escalate: bool
    reason_code: str


class RetrievedReference(StrictModel):
    document_id: str
    chunk_id: str
    title: str
    content_excerpt: str
    score: float = Field(ge=0.0, le=1.0)


class CaseExecutionResult(StrictModel):
    case_id: Annotated[str, Field(pattern=r"^[A-Z]{3}-\d{3}$")]
    predicted_intent: Intent | str | None = None
    actual_handoffs: list[ExpectedHandoff] = Field(default_factory=list)
    actual_tools: list[str] = Field(default_factory=list)
    tool_outcomes: list[str] = Field(default_factory=list)
    predicted_risk: RiskLevel | str | None = None
    actual_require_human: bool | None = None
    actual_outcome: TicketStatus | str | None = None
    retrieved_document_ids: list[str] = Field(default_factory=list)
    actual_business_steps: int | None = None
    latency_ms: float = 0.0
    high_risk_bypass: bool = False
    policy_decisions: list[PolicyDecisionObservation] = Field(default_factory=list)
    routing_decisions: list[RoutingDecisionObservation] = Field(default_factory=list)
    tool_error_codes: list[str] = Field(default_factory=list)
    cost_usd: Decimal = Decimal("0")
    error_code: str | None = None


class MetricsSummary(StrictModel):
    intent_accuracy: Decimal
    handoff_accuracy: Decimal
    tool_selection_jaccard: Decimal
    policy_decision_accuracy: Decimal
    high_risk_bypass_count: int
    task_success_rate: Decimal
    retrieval_precision: Decimal
    retrieval_recall: Decimal
    trajectory_efficiency: Decimal
    redundant_tool_call_rate: Decimal
    latency_p95_ms: float
    human_escalation_rate: Decimal
    total_cases: int
    failed_cases: int
    policy_decisions_evaluated: int
    policy_decision_coverage: Decimal


class MetricCheck(StrictModel):
    name: str
    value: float | int
    threshold: float | int
    comparison: str
    passed: bool


class ThresholdGateResult(StrictModel):
    passed: bool
    checks: list[MetricCheck]
    details: dict[str, Any] = Field(default_factory=dict)


class EvalCaseResult(CaseExecutionResult):
    query: str
    expected_intent: Intent
    expected_handoffs: list[ExpectedHandoff]
    expected_tools: list[ToolName]
    expected_risk: RiskLevel
    expected_require_human: bool
    expected_outcome: TicketStatus
    expected_relevant_document_ids: list[str]
    observed_graph_nodes: list[str] = Field(default_factory=list)
    graph_step_count: int | None = Field(default=None, ge=0)
    retrieved_references: list[RetrievedReference] = Field(default_factory=list)
    failure_code: str | None = None


class ComparisonResult(StrictModel):
    baseline_run_id: str
    baseline_path: str
    matched_dataset_version: bool
    matched_stage: bool
    matched_model: bool
    matched_base_url_id: bool
    prompt_version_differences: dict[str, str] = Field(default_factory=dict)


class EvalArtifact(StrictModel):
    artifact_version: Literal["2.1.0"] = "2.1.0"
    run_id: str
    mode: Literal["offline", "live"]
    stage: Literal["model-baseline", "e2e-candidate"]
    dataset_version: str
    dataset_level: str
    dataset_review_status: str
    model: str
    model_seed: int = Field(default=42, ge=0)
    model_think: bool | None = None
    base_url_id: str
    database_name: str | None = None
    prompt_versions: dict[str, str]
    git_sha: str
    worktree_clean: bool
    worktree_diff_sha256: str
    created_at: AwareDatetime
    case_count: int
    concurrency: int = Field(default=1, ge=1, le=8)
    max_observed_concurrency: int = Field(default=1, ge=1, le=8)
    cases: list[EvalCaseResult]
    intent_match_count: int
    failure_counts: dict[str, int]
    metrics: dict[str, Any] | None = None
    threshold_gate: dict[str, Any] | None = None
    comparison: ComparisonResult | None = None
    cost_usd: Annotated[
        Decimal,
        Field(ge=Decimal("0"), max_digits=12, decimal_places=6),
    ] = Decimal("0")
    cost_basis: str
    interpretation: str
