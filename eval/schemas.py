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


class CaseExecutionResult(StrictModel):
    case_id: Annotated[str, Field(pattern=r"^[A-Z]{3}-\d{3}$")]
    predicted_intent: Intent | str | None = None
    actual_handoffs: list[ExpectedHandoff] = Field(default_factory=list)
    actual_tools: list[str] = Field(default_factory=list)
    predicted_risk: RiskLevel | str | None = None
    actual_require_human: bool | None = None
    actual_outcome: TicketStatus | str | None = None
    retrieved_document_ids: list[str] = Field(default_factory=list)
    actual_business_steps: int = 1
    latency_ms: float = 0.0
    high_risk_bypass: bool = False
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
