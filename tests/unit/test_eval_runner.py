"""Unit tests for stage separation, candidate metadata and compare semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.run_eval import (
    compare_with,
    comparison_is_comparable,
    host_label,
    main,
)
from eval.schemas import EvalArtifact


def test_offline_eval_is_classification_harness_only(tmp_path: Path) -> None:
    output_file = tmp_path / "test-artifact.json"

    ret = main(
        [
            "--mode",
            "offline",
            "--stage",
            "model-baseline",
            "--output",
            str(output_file),
        ]
    )

    assert ret == 0
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["case_count"] == 80
    assert data["intent_match_count"] == 80
    assert data["mode"] == "offline"
    assert data["stage"] == "model-baseline"
    assert data["metrics"]["scope"] == "classification_only"
    assert data["metrics"]["evaluated_cases"] == 80
    assert "high_risk_bypass_count" not in data["metrics"]
    assert all(case["actual_tools"] == [] for case in data["cases"])
    assert data["worktree_diff_sha256"]
    assert "not a measurement" in data["interpretation"]


def test_e2e_candidate_refuses_offline_fakes(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="requires --mode live"):
        main(
            [
                "--mode",
                "offline",
                "--stage",
                "e2e-candidate",
                "--output",
                str(tmp_path / "artifact.json"),
            ]
        )


def test_prompt_version_changes_are_reported_but_comparison_remains_comparable(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "run_id": "live-model-baseline-001",
                "dataset_version": "1.0.0",
                "stage": "e2e-candidate",
                "model": "qwen3:8b",
                "base_url_id": "http://localhost:11434/v1",
                "prompt_versions": {"action": "1.0.0", "supervisor": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    artifact = EvalArtifact(
        run_id="live-e2e-candidate-002",
        mode="live",
        stage="e2e-candidate",
        dataset_version="1.0.0",
        dataset_level="full",
        dataset_review_status="reviewed",
        model="qwen3:8b",
        model_seed=42,
        model_think=None,
        base_url_id="http://localhost:11434/v1",
        prompt_versions={"action": "1.0.1", "supervisor": "1.0.0"},
        git_sha="0" * 40,
        worktree_clean=False,
        worktree_diff_sha256="1" * 64,
        created_at="2026-09-20T00:00:00Z",
        case_count=0,
        cases=[],
        intent_match_count=0,
        failure_counts={},
        cost_basis="local_ollama_no_provider_billing",
        interpretation="candidate",
    )

    comparison = compare_with(baseline_path, artifact)

    assert comparison.prompt_version_differences == {"action": "1.0.0 -> 1.0.1"}
    assert comparison_is_comparable(comparison)


def test_local_ollama_aliases_have_the_same_endpoint_identity() -> None:
    assert host_label("http://localhost:11434/v1") == "http://local-ollama:11434/v1"
    assert host_label("http://host.docker.internal:11434/v1") == (
        "http://local-ollama:11434/v1"
    )


def test_comparison_normalizes_local_ollama_aliases(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "run_id": "live-model-baseline-001",
                "dataset_version": "1.0.0",
                "stage": "model-baseline",
                "model": "qwen3:8b",
                "base_url_id": "http://localhost:11434/v1",
                "prompt_versions": {"supervisor": "1.0.1"},
            }
        ),
        encoding="utf-8",
    )
    artifact = EvalArtifact(
        run_id="live-model-baseline-002",
        mode="live",
        stage="model-baseline",
        dataset_version="1.0.0",
        dataset_level="full",
        dataset_review_status="reviewed",
        model="qwen3:8b",
        model_seed=42,
        model_think=False,
        base_url_id="http://host.docker.internal:11434/v1",
        prompt_versions={"supervisor": "1.0.5"},
        git_sha="0" * 40,
        worktree_clean=False,
        worktree_diff_sha256="1" * 64,
        created_at="2026-09-20T00:00:00Z",
        case_count=0,
        cases=[],
        intent_match_count=0,
        failure_counts={},
        cost_basis="local_ollama_no_provider_billing",
        interpretation="candidate",
    )

    comparison = compare_with(baseline_path, artifact)

    assert comparison.matched_base_url_id is True
    assert comparison_is_comparable(comparison)
