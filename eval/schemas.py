"""Evaluation dataset and artifact contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

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
