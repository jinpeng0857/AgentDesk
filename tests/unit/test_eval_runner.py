"""Unit tests for the evaluation runner and baseline updater."""

from __future__ import annotations

import json
from pathlib import Path

from eval.run_eval import main


def test_offline_eval_runner_generates_valid_artifact_with_metrics(tmp_path: Path) -> None:
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
    assert output_file.exists()

    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["case_count"] == 80
    assert data["intent_match_count"] == 80
    assert data["mode"] == "offline"
    assert "metrics" in data
    assert data["metrics"]["intent_accuracy"] == "1.0000"
    assert data["metrics"]["high_risk_bypass_count"] == 0
