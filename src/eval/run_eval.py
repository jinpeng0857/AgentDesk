"""Classification-only and real-graph AgentDesk evaluation runners.

Offline mode is a deterministic harness self-test. ``model-baseline`` measures
only classifications. ``e2e-candidate`` runs every reviewed golden case through
the real LangGraph, tool gateway, PostgreSQL and checkpoint adapter; its metrics
are observations and are never copied from expected labels.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, SecretStr

from agents.context import ModelCallConfig, ModelClient, ModelMessage
from agents.schemas import Intent, RoutingDecision, RoutingReason
from backend.config import AppSettings
from backend.integrations.model_client import (
    ModelClientError,
    OpenAICompatibleModelClient,
)
from backend.integrations.prompt_loader import build_system_message, load_all_prompts
from eval.e2e import (
    DEFAULT_EVAL_DATABASE_URL,
    LiveEvalConfigurationError,
    run_e2e_candidate,
)
from eval.metrics import compute_all_metrics, compute_intent_accuracy, evaluate_thresholds
from eval.schemas import (
    CaseExecutionResult,
    ComparisonResult,
    EvalArtifact,
    EvalCaseResult,
    GoldenCase,
    GoldenDataset,
    MetricsSummary,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPOSITORY_ROOT / "resources" / "evaluation" / "golden_dataset.json"
DEFAULT_CANDIDATE_DIR = REPOSITORY_ROOT / "artifacts" / "evaluation" / "candidates"
OFFLINE_MODEL = "offline-deterministic-fake"
DEFAULT_LIVE_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
OFFLINE_BASE_URL_ID = "offline-deterministic-fake"
OFFLINE_INTERPRETATION = (
    "Offline harness self-test using a deterministic fake model keyed to the reviewed "
    "intent labels. It verifies prompt loading, structured validation and artifact "
    "metadata only; it is not a measurement of model or system quality."
)
LIVE_CLASSIFICATION_INTERPRETATION = (
    "Live model-baseline: only actual Supervisor intent classifications and measured "
    "wall-clock latency are evaluated. This does not measure graph, tools or task success."
)
LIVE_E2E_INTERPRETATION = (
    "Live e2e-candidate: each case was run through the actual graph and persisted "
    "tool/checkpoint path. Case observations are derived from graph state, audit and "
    "PostgreSQL projections; expected labels are used only for post-run scoring. "
    "This is a review candidate and does not update the official baseline."
)

def git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_output(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    )
    return result.stdout


def worktree_clean() -> bool:
    return not _git_output("status", "--porcelain").strip()


def worktree_diff_sha256() -> str:
    """Fingerprint tracked and untracked source changes without recording contents."""
    digest = hashlib.sha256()
    digest.update(_git_output("diff", "HEAD", "--binary"))
    untracked = _git_output("ls-files", "--others", "--exclude-standard")
    for raw_path in sorted(untracked.splitlines()):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        candidate = (REPOSITORY_ROOT / relative).resolve()
        try:
            candidate.relative_to(REPOSITORY_ROOT.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(candidate.read_bytes())
    return digest.hexdigest()


def host_label(base_url: str) -> str:
    """Record endpoint identity without credentials or query-string secrets."""
    parsed = urlparse(base_url)
    if parsed.hostname is None:
        return "unknown-host"
    authority = parsed.hostname.lower()
    # Windows and Compose use different aliases for the same local Ollama
    # service. Normalize only these explicit local aliases for comparisons.
    if authority in {"localhost", "127.0.0.1", "host.docker.internal"}:
        authority = "local-ollama"
    if parsed.port is not None:
        authority = f"{authority}:{parsed.port}"
    return f"{parsed.scheme.lower()}://{authority}{parsed.path.rstrip('/')}"


def load_dataset(path: Path) -> GoldenDataset:
    return GoldenDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


class DeterministicLabelModel:
    """Deterministic test double for classifier/artifact harness validation only."""

    def __init__(self, labels: Mapping[str, Intent]) -> None:
        self._labels = dict(labels)

    async def complete_structured(
        self,
        *,
        messages: Sequence[ModelMessage],
        output_model: type[ModelT],
        config: ModelCallConfig,
    ) -> ModelT:
        del config
        query = messages[-1].content
        if query not in self._labels:
            raise ModelClientError("offline model has no response for this query")
        payload = {
            "kind": "route",
            "intent": self._labels[query].value,
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
) -> tuple[RoutingDecision | None, str | None, float]:
    messages = [
        ModelMessage(role="system", content=system_message),
        ModelMessage(role="user", content=case.query),
    ]
    started = asyncio.get_running_loop().time()
    try:
        result = await client.complete_structured(
            messages=messages,
            output_model=RoutingDecision,
            config=config,
        )
    except ModelClientError as exc:
        elapsed_ms = max(
            0.0, (asyncio.get_running_loop().time() - started) * 1000
        )
        return None, type(exc).__name__, elapsed_ms
    elapsed_ms = max(0.0, (asyncio.get_running_loop().time() - started) * 1000)
    return result, None, elapsed_ms


def compare_with(baseline_path: Path, artifact: EvalArtifact) -> ComparisonResult:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    old_prompts = baseline.get("prompt_versions", {})
    new_prompts = artifact.prompt_versions
    if not isinstance(old_prompts, dict):
        old_prompts = {}
    prompt_differences = {
        agent: f"{old_prompts.get(agent, '<missing>')} -> {new_prompts.get(agent, '<missing>')}"
        for agent in sorted(set(old_prompts) | set(new_prompts))
        if old_prompts.get(agent) != new_prompts.get(agent)
    }
    return ComparisonResult(
        baseline_run_id=str(baseline.get("run_id", "unknown")),
        baseline_path=str(baseline_path),
        matched_dataset_version=baseline.get("dataset_version") == artifact.dataset_version,
        matched_stage=baseline.get("stage") == artifact.stage,
        matched_model=baseline.get("model") == artifact.model,
        matched_base_url_id=(
            host_label(str(baseline.get("base_url_id", "unknown-host")))
            == host_label(artifact.base_url_id)
        ),
        prompt_version_differences=prompt_differences,
    )


def comparison_is_comparable(comparison: ComparisonResult) -> bool:
    """Prompt edits are reported but do not invalidate same-dataset/model comparisons."""
    return (
        comparison.matched_dataset_version
        and comparison.matched_stage
        and comparison.matched_model
        and comparison.matched_base_url_id
    )


def _live_configuration(args: argparse.Namespace) -> tuple[str, str, SecretStr]:
    loaded_settings = AppSettings()
    model = (
        args.model
        or os.environ.get("OPENAI_MODEL")
        or loaded_settings.openai_model
        or DEFAULT_LIVE_MODEL
    )
    configured_url = os.environ.get("OPENAI_BASE_URL") or loaded_settings.openai_base_url
    base_url = args.base_url or (
        configured_url
        if configured_url != "https://api.openai.com/v1"
        else DEFAULT_OLLAMA_BASE_URL
    )
    api_key_text = args.api_key or os.environ.get("OPENAI_API_KEY") or None
    if api_key_text is None and loaded_settings.openai_api_key is not None:
        api_key_text = loaded_settings.openai_api_key.get_secret_value()
    if not api_key_text:
        api_key_text = "ollama"
    if not model.strip():
        raise SystemExit("live evaluation requires a non-empty --model")
    return model, base_url, SecretStr(api_key_text)


def _case_expectations(case: GoldenCase) -> dict[str, object]:
    return {
        "expected_intent": case.expected_intent,
        "expected_handoffs": case.expected_handoffs,
        "expected_tools": case.expected_tools,
        "expected_risk": case.expected_risk,
        "expected_require_human": case.expected_require_human,
        "expected_outcome": case.expected_outcome,
        "expected_relevant_document_ids": case.relevant_document_ids,
    }


async def run(args: argparse.Namespace) -> EvalArtifact:
    dataset = load_dataset(args.dataset)
    prompts = load_all_prompts()
    clean = worktree_clean()
    sha = git_sha()
    diff_sha = worktree_diff_sha256()
    run_id = f"{args.mode}-{args.stage}-{uuid.uuid4().hex[:12]}"
    model_name = OFFLINE_MODEL
    base_url_id = OFFLINE_BASE_URL_ID
    database_name: str | None = None
    threshold_gate: dict[str, object] | None = None

    if args.stage == "e2e-candidate" and args.mode != "live":
        raise SystemExit(
            "e2e-candidate requires --mode live; offline fakes cannot prove system quality"
        )

    if args.mode == "offline":
        client: ModelClient = DeterministicLabelModel(
            {case.query: case.expected_intent for case in dataset.cases}
        )
        model_name = OFFLINE_MODEL
        system_message = build_system_message(
            prompts["supervisor"], user_query="<case query is supplied separately>"
        )
        results: list[EvalCaseResult] = []
        for case in dataset.cases:
            decision, failure, latency_ms = await classify_case(
                client,
                system_message,
                case,
                ModelCallConfig(
                    model=OFFLINE_MODEL,
                    prompt_version=prompts["supervisor"].version,
                ),
            )
            results.append(
                EvalCaseResult(
                    case_id=case.case_id,
                    query=case.query,
                    **_case_expectations(case),
                    predicted_intent=decision.intent if decision is not None else None,
                    latency_ms=latency_ms,
                    error_code=failure,
                    failure_code=failure,
                )
            )
        interpretation = OFFLINE_INTERPRETATION
        cost_basis = "not_billed_deterministic_fake"
        classification_only = True
        metrics: dict[str, object] | None = None
    else:
        model_name, base_url, api_key = _live_configuration(args)
        base_url_id = host_label(base_url)
        settings = AppSettings(
            database_url=args.database_url or os.environ.get(
                "EVAL_TEST_DATABASE_URL", DEFAULT_EVAL_DATABASE_URL
            ),
            app_env="test",
            jwt_secret="agentdesk-live-eval-secret-is-local-and-test-only",
            openai_api_key=api_key,
            openai_base_url=base_url,
            openai_model=model_name,
            openai_think=False,
            openai_timeout_s=args.timeout,
            openai_max_retries=args.max_retries,
        )
        client = OpenAICompatibleModelClient(
            api_key=api_key,
            base_url=base_url,
        )

        if args.stage == "model-baseline":
            system_message = build_system_message(
                prompts["supervisor"], user_query="<case query is supplied separately>"
            )
            results = []
            config = ModelCallConfig(
                model=model_name,
                prompt_version=prompts["supervisor"].version,
                timeout_s=args.timeout,
                max_retries=args.max_retries,
                think=False,
            )
            for index, case in enumerate(dataset.cases, start=1):
                decision, failure, latency_ms = await classify_case(
                    client, system_message, case, config
                )
                results.append(
                    EvalCaseResult(
                        case_id=case.case_id,
                        query=case.query,
                        **_case_expectations(case),
                        predicted_intent=decision.intent if decision is not None else None,
                        latency_ms=latency_ms,
                        error_code=failure,
                        failure_code=failure,
                    )
                )
                print(
                    f"[{index}/{len(dataset.cases)}] {case.case_id} "
                    f"intent={decision.intent.value if decision is not None else 'error'} "
                    f"latency_ms={latency_ms:.2f}",
                    flush=True,
                )
            interpretation = LIVE_CLASSIFICATION_INTERPRETATION
            cost_basis = "local_ollama_no_provider_billing"
            classification_only = True
            metrics = None
        else:
            database_url = settings.database_url
            try:
                from eval.e2e import require_isolated_test_database

                database_name = require_isolated_test_database(database_url)
            except LiveEvalConfigurationError as exc:
                raise SystemExit(str(exc)) from exc

            def report_progress(index: int, total: int, case_id: str) -> None:
                print(f"[{index}/{total}] {case_id} graph-complete", flush=True)

            try:
                results, database_name = await run_e2e_candidate(
                    dataset=dataset,
                    model_client=client,
                    prompts=prompts,
                    database_url=database_url,
                    model_name=model_name,
                    base_url=base_url,
                    timeout_s=args.timeout,
                    max_retries=args.max_retries,
                    progress=report_progress,
                )
            except LiveEvalConfigurationError as exc:
                raise SystemExit(str(exc)) from exc
            interpretation = LIVE_E2E_INTERPRETATION
            cost_basis = "local_ollama_no_provider_billing"
            classification_only = False
            metrics = None

    intent_matches = sum(
        1
        for result in results
        if result.predicted_intent is not None
        and result.predicted_intent == result.expected_intent
    )
    failure_counts = dict(
        sorted(Counter(result.failure_code for result in results if result.failure_code).items())
    )
    artifact = EvalArtifact(
        run_id=run_id,
        mode=args.mode,
        stage=args.stage,
        dataset_version=dataset.version,
        dataset_level=dataset.level,
        dataset_review_status=dataset.review_status,
        model=model_name,
        model_seed=42,
        model_think=False if args.mode == "live" else None,
        base_url_id=base_url_id,
        database_name=database_name,
        prompt_versions={agent: prompt.version for agent, prompt in prompts.items()},
        git_sha=sha,
        worktree_clean=clean,
        worktree_diff_sha256=diff_sha,
        created_at=datetime.now(UTC),
        case_count=len(dataset.cases),
        cases=results,
        intent_match_count=intent_matches,
        failure_counts=failure_counts,
        metrics=metrics,
        threshold_gate=threshold_gate,
        cost_usd=Decimal("0"),
        cost_basis=cost_basis,
        interpretation=interpretation,
    )

    if classification_only:
        metric_results = [
            CaseExecutionResult(
                case_id=result.case_id,
                predicted_intent=result.predicted_intent,
                latency_ms=result.latency_ms,
                error_code=result.error_code,
            )
            for result in results
        ]
        intent_accuracy = compute_intent_accuracy(metric_results, dataset)
        artifact = artifact.model_copy(
            update={
                "metrics": {
                    "scope": "classification_only",
                    "evaluated_cases": len(results),
                    "intent_accuracy": str(intent_accuracy),
                    "intent_match_count": intent_matches,
                    "latency_p95_ms": max(
                        (result.latency_ms for result in results), default=0.0
                    ),
                }
            }
        )
    else:
        metric_results = [
            CaseExecutionResult.model_validate(
                result.model_dump(include=set(CaseExecutionResult.model_fields))
            )
            for result in results
        ]
        summary = compute_all_metrics(metric_results, dataset)
        measured_baseline: MetricsSummary | None = None
        if args.compare is not None:
            baseline_data = json.loads(args.compare.read_text(encoding="utf-8"))
            if isinstance(baseline_data, dict) and isinstance(baseline_data.get("metrics"), dict):
                try:
                    measured_baseline = MetricsSummary.model_validate(baseline_data["metrics"])
                except ValueError:
                    measured_baseline = None
        gate = evaluate_thresholds(summary, measured_baseline)
        artifact = artifact.model_copy(
            update={
                "metrics": summary.model_dump(mode="json"),
                "threshold_gate": gate.model_dump(mode="json"),
            }
        )

    if args.compare is not None:
        comparison = compare_with(args.compare, artifact)
        if not comparison_is_comparable(comparison):
            raise SystemExit(
                "refusing to compare artifacts with different dataset, stage, model or endpoint: "
                f"{comparison.model_dump(mode='json')}"
            )
        artifact = artifact.model_copy(update={"comparison": comparison})

    return artifact


def _write_artifact(artifact: EvalArtifact, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    output.write_text(serialized, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentDesk staged evaluation runner")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument(
        "--stage", choices=("model-baseline", "e2e-candidate"), default="model-baseline"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--compare", type=Path, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write an additional review candidate; never modifies the official baseline",
    )
    args = parser.parse_args(argv)

    if args.timeout <= 0 or args.timeout > 120:
        parser.error("--timeout must be greater than 0 and at most 120 seconds")
    if args.max_retries < 0 or args.max_retries > 2:
        parser.error("--max-retries must be between 0 and 2")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    artifact = asyncio.run(run(args))
    output = args.output or DEFAULT_CANDIDATE_DIR / f"{artifact.run_id}.json"
    _write_artifact(artifact, output)
    print(f"Wrote eval artifact: {output}")

    if args.update_baseline:
        candidate_path = DEFAULT_CANDIDATE_DIR / f"{artifact.run_id}-review-candidate.json"
        _write_artifact(artifact, candidate_path)
        print(f"Wrote review candidate only: {candidate_path}; official baseline unchanged")

    print(
        f"stage={artifact.stage} cases={artifact.case_count} "
        f"intent_match_count={artifact.intent_match_count} failures={artifact.failure_counts}"
    )
    print(artifact.interpretation)
    if artifact.threshold_gate is not None:
        passed = bool(artifact.threshold_gate.get("passed", False))
        for check in artifact.threshold_gate.get("checks", []):
            if isinstance(check, dict):
                print(
                    f"gate {check.get('name')}: {check.get('value')} "
                    f"{check.get('comparison')} {check.get('threshold')} "
                    f"passed={check.get('passed')}"
                )
        return 0 if passed else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
