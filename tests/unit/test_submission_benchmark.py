"""Regression tests for durable HTTP timing records and percentile reporting."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.acceptance.benchmark_ticket_submission import (
    append_response_record,
    nearest_rank_p95,
)


def test_nearest_rank_p95_is_not_max_for_twenty_requests() -> None:
    assert nearest_rank_p95([float(value) for value in range(1, 21)]) == 19.0


def test_completed_http_timing_is_appended_as_a_durable_jsonl_record(
    tmp_path: Path,
) -> None:
    output = tmp_path / "responses.jsonl"
    append_response_record(
        output,
        {"case_id": "NET-001", "status_code": 202, "latency_ms": 123.4},
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert records == [{"case_id": "NET-001", "status_code": 202, "latency_ms": 123.4}]
