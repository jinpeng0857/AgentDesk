"""Phase 1 evaluation artifact writer.

This runner records a reproducible artifact for a model classification baseline.
It never writes ``eval/baseline.json``, never promotes a design threshold into a
score, and refuses to run a live baseline from a dirty worktree so the recorded
Git SHA always identifies the evaluated code.

Offline mode drives a deterministic fake. That proves the harness — prompt
loading, the structured-output boundary, metadata capture and failure codes —
and proves nothing about prompt or model quality. Every artifact says so in
``interpretation``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from agents.context import ModelCallConfig, ModelClient, ModelMessage
from agents.schemas import Intent, RoutingDecision, RoutingReason
from backend.config import AppSettings, ConfigurationError
from backend.integrations.model_client import (
    ModelClientError,
    OpenAICompatibleModelClient,
)
from backend.integrations.prompt_loader import build_system_message, load_all_prompts
from eval.schemas import GoldenCase, GoldenDataset

ModelT = TypeVar("ModelT", bound=BaseModel)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPOSITORY_ROOT / "eval" / "golden_dataset.json"
OFFLINE_BASE_URL_ID = "offline-deterministic-fake"
OFFLINE_INTERPRETATION = (
    "Offline harness self-test using a deterministic fake model. It verifies prompt "
    "loading, the structured-output boundary and artifact metadata. It is not a "
    "measurement of prompt, model or system quality."
)
LIVE_INTERPRETATION = (
    "Live model classification baseline. Counts are raw observations on the named "
    "dataset version and are not project achievements."
)

Mode = Literal["offline", "live"]
Stage = Literal["model-baseline", "e2e-candidate"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseResult(StrictModel):
    case_id: str
    query: str
    expected_intent: Intent
    predicted_intent: Intent | None
    failure_code: str | None


class ComparisonResult(StrictModel):
    baseline_run_id: str
    baseline_path: str
    matched_dataset_version: bool
    matched_model: bool
    matched_base_url_id: bool
    matched_prompt_versions: bool


class EvalArtifact(StrictModel):
    run_id: str
    mode: Mode
    stage: Stage
    dataset_version: str
    dataset_level: str
    dataset_review_status: str
    model: str
    base_url_id: str
    prompt_versions: dict[str, str]
    git_sha: str
    worktree_clean: bool
    created_at: datetime
    case_count: int
    cases: list[CaseResult]
    intent_match_count: int
    failure_counts: dict[str, int]
    comparison: ComparisonResult | None = None
    cost_usd: Decimal = Field(default=Decimal("0"))
    interpretation: str


def git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def worktree_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return not result.stdout.strip()


def host_label(base_url: str) -> str:
    """Record only the endpoint host, never a full URL that could carry a secret."""
    return urlparse(base_url).hostname or "unknown-host"


def load_dataset(path: Path) -> GoldenDataset:
    return GoldenDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


def deterministic_response(case: GoldenCase) -> RoutingDecision:
    """Offline stand-in keyed to the reviewed label, not to any model behaviour."""
    return RoutingDecision(
        intent=case.expected_intent,
        next_agent="knowledge",
        confidence=0.99,
        escalate=False,
        reason_code=RoutingReason.NEEDS_KNOWLEDGE,
    )


class DeterministicLabelModel:
    """Offline model stand-in that replays the reviewed label for each query.

    It satisfies the same ``ModelClient`` contract as the real adapter, so the
    offline run exercises prompt loading, structured validation and failure
    classification — and nothing about model quality.
    """

    def __init__(self, labels: Mapping[str, Intent]) -> None:
        self._labels = dict(labels)
        self.calls = 0

    async def complete_structured(
        self,
        *,
        messages: Sequence[ModelMessage],
        output_model: type[ModelT],
        config: ModelCallConfig,
    ) -> ModelT:
        del config
        self.calls += 1
        query = messages[-1].content
        if query not in self._labels:
            raise ModelClientError(f"offline stand-in has no label for query {query!r}")
        payload = {
            "kind": "route",
            "intent": str(self._labels[query]),
            "next_agent": "knowledge",
            "confidence": 0.99,
            "escalate": False,
            "reason_code": RoutingReason.NEEDS_KNOWLEDGE.value,
        }
        return output_model.model_validate(payload)


async def classify_case(
    client: ModelClient,
    system_message: str,
    case: GoldenCase,
    config: ModelCallConfig,
) -> tuple[RoutingDecision | None, str | None]:
    messages = [
        ModelMessage(role="system", content=system_message),
        ModelMessage(role="user", content=case.query),
    ]
    try:
        return (
            await client.complete_structured(
                messages=messages, output_model=RoutingDecision, config=config
            ),
            None,
        )
    except ModelClientError as exc:
        return None, type(exc).__name__


def compare_with(baseline_path: Path, artifact: EvalArtifact) -> ComparisonResult:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    return ComparisonResult(
        baseline_run_id=str(baseline.get("run_id", "unknown")),
        baseline_path=str(baseline_path),
        matched_dataset_version=baseline.get("dataset_version") == artifact.dataset_version,
        matched_model=baseline.get("model") == artifact.model,
        matched_base_url_id=baseline.get("base_url_id") == artifact.base_url_id,
        matched_prompt_versions=baseline.get("prompt_versions") == artifact.prompt_versions,
    )


def comparison_is_comparable(comparison: ComparisonResult) -> bool:
    return (
        comparison.matched_dataset_version
        and comparison.matched_model
        and comparison.matched_base_url_id
        and comparison.matched_prompt_versions
    )


async def run(args: argparse.Namespace) -> EvalArtifact:
    settings = AppSettings()
    dataset = load_dataset(args.dataset)
    prompts = load_all_prompts()
    clean = worktree_clean()
    sha = git_sha()

    config = ModelCallConfig(
        model=settings.openai_model or "offline-deterministic-fake",
        prompt_version=prompts["supervisor"].version,
    )

    client: ModelClient
    if args.mode == "live":
        if not clean:
            raise SystemExit(
                "refusing to run a live baseline from a dirty worktree: "
                "the recorded Git SHA must identify the evaluated code"
            )
        try:
            settings.validate_readiness(require_model=True)
        except ConfigurationError as exc:
            raise SystemExit(f"live mode is not configured: {exc}") from exc
        if settings.openai_api_key is None:
            raise SystemExit(
                "live mode requires OPENAI_API_KEY; no artifact was written"
            )
        client = OpenAICompatibleModelClient.from_settings(settings)
        base_url_id = host_label(settings.openai_base_url)
        interpretation = LIVE_INTERPRETATION
        model_name = settings.openai_model
    else:
        base_url_id = OFFLINE_BASE_URL_ID
        interpretation = OFFLINE_INTERPRETATION
        model_name = config.model
        client = DeterministicLabelModel(
            {case.query: case.expected_intent for case in dataset.cases}
        )

    system_message = build_system_message(
        prompts["supervisor"], user_query="<see per-case user message>"
    )

    results: list[CaseResult] = []
    intent_matches = 0
    failure_counts: dict[str, int] = {}

    for case in dataset.cases:
        decision, failure = await classify_case(client, system_message, case, config)
        if decision is not None and decision.intent is case.expected_intent:
            intent_matches += 1
        if failure is not None:
            failure_counts[failure] = failure_counts.get(failure, 0) + 1
        results.append(
            CaseResult(
                case_id=case.case_id,
                query=case.query,
                expected_intent=case.expected_intent,
                predicted_intent=decision.intent if decision is not None else None,
                failure_code=failure,
            )
        )

    artifact = EvalArtifact(
        run_id=f"{args.mode}-{args.stage}-{uuid.uuid4().hex[:12]}",
        mode=args.mode,
        stage=args.stage,
        dataset_version=dataset.version,
        dataset_level=dataset.level,
        dataset_review_status=dataset.review_status,
        model=model_name,
        base_url_id=base_url_id,
        prompt_versions={agent: prompt.version for agent, prompt in prompts.items()},
        git_sha=sha,
        worktree_clean=clean,
        created_at=datetime.now(UTC),
        case_count=len(dataset.cases),
        cases=results,
        intent_match_count=intent_matches,
        failure_counts=failure_counts,
        cost_usd=Decimal("0"),
        interpretation=interpretation,
    )

    if args.compare is not None:
        comparison = compare_with(args.compare, artifact)
        if not comparison_is_comparable(comparison):
            raise SystemExit(
                "refusing to compare artifacts with different dataset, model, "
                f"endpoint or prompt metadata: {comparison.model_dump()}"
            )
        artifact = artifact.model_copy(update={"comparison": comparison})

    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentDesk phase 1 eval runner")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument(
        "--stage", choices=("model-baseline", "e2e-candidate"), default="model-baseline"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path, default=None)
    args = parser.parse_args(argv)

    import asyncio

    artifact = asyncio.run(run(args))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote eval artifact: {args.output}")
    print(
        f"cases={artifact.case_count} intent_match_count={artifact.intent_match_count} "
        f"failures={artifact.failure_counts}"
    )
    print(artifact.interpretation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
