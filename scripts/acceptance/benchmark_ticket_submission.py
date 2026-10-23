"""Measure concurrent FastAPI ticket admission, not Worker execution throughput."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from eval.run_eval import (  # noqa: E402
    git_sha,
    host_label,
    load_dataset,
    worktree_clean,
    worktree_diff_sha256,
)


def nearest_rank_p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (95 * len(ordered) + 99) // 100)
    return round(ordered[rank - 1], 2)


def append_response_record(path: Path, record: dict[str, Any]) -> None:
    """Durably append each completed request before later aggregation can fail."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    username = os.environ.get("AGENTDESK_BENCH_USERNAME")
    password = os.environ.get("AGENTDESK_BENCH_PASSWORD")
    if not username or not password:
        raise SystemExit("Set AGENTDESK_BENCH_USERNAME and AGENTDESK_BENCH_PASSWORD")

    dataset = load_dataset(args.dataset)
    cases = dataset.cases[: args.limit] if args.limit is not None else dataset.cases
    if not cases:
        raise SystemExit("Dataset selection is empty")

    run_id = f"api-admission-{uuid4().hex[:12]}"
    responses_path = args.output.with_suffix(".responses.jsonl")
    if args.output.exists() or responses_path.exists():
        raise SystemExit(
            f"Refusing to overwrite existing benchmark output: {args.output} "
            "or its response log"
        )
    responses_path.parent.mkdir(parents=True, exist_ok=True)
    responses_path.write_text("", encoding="utf-8", newline="\n")
    records: list[dict[str, Any]] = []
    write_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.timeout_s)
    batch_started = time.perf_counter()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
        login_started = time.perf_counter()
        login = await client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        if login.status_code != 200:
            raise SystemExit(f"Login failed with HTTP {login.status_code}")
        token = login.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise SystemExit("Login response did not contain an access token")
        headers = {"Authorization": f"Bearer {token}"}
        login_elapsed_ms = (time.perf_counter() - login_started) * 1000

        async def submit(index: int, case: Any) -> None:
            record: dict[str, Any] = {
                "index": index,
                "case_id": case.case_id,
                "status_code": None,
                "latency_ms": None,
                "error_type": None,
            }
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.post(
                        "/api/tickets",
                        json={"user_query": case.query},
                        headers=headers,
                    )
                    record["status_code"] = response.status_code
                    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                except httpx.HTTPError as exc:
                    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                    record["error_type"] = type(exc).__name__
                record["completed_offset_s"] = time.perf_counter() - batch_started
            async with write_lock:
                append_response_record(responses_path, record)
                records.append(record)

        await asyncio.gather(*(submit(index, case) for index, case in enumerate(cases)))
        batch_elapsed_s = max(
            (float(record["completed_offset_s"]) for record in records), default=0.0
        )

    latencies = [
        float(record["latency_ms"])
        for record in records
        if isinstance(record.get("latency_ms"), (float, int))
    ]
    accepted = sum(record["status_code"] == 202 for record in records)
    complete = (
        len(records) == len(cases)
        and len(latencies) == len(cases)
        and all(isinstance(record.get("status_code"), int) for record in records)
    )
    return {
        "artifact_type": "fastapi_ticket_admission_benchmark",
        "artifact_version": "1.0.0",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "worktree_clean": worktree_clean(),
        "worktree_diff_sha256": worktree_diff_sha256(),
        "base_url_id": host_label(args.base_url),
        "dataset_version": dataset.version,
        "dataset_level": dataset.level if len(cases) == len(dataset.cases) else "smoke",
        "selected_case_ids": [case.case_id for case in cases],
        "requested": len(cases),
        "completed_http_requests": len(records),
        "accepted_202": accepted,
        "configured_request_concurrency": args.concurrency,
        "batch_elapsed_seconds": round(batch_elapsed_s, 4),
        "admission_throughput_requests_per_second": round(
            accepted / batch_elapsed_s, 4
        ) if batch_elapsed_s > 0 else None,
        "http_response_latency_p95_ms_nearest_rank": (
            nearest_rank_p95(latencies) if complete else None
        ),
        "http_response_p95_complete_sample": complete,
        "login_latency_ms_excluded_from_request_p95": round(login_elapsed_ms, 2),
        "response_records_path": str(responses_path),
        "worker_execution_measured": False,
        "worker_throughput_measured": False,
        "scope": (
            "FastAPI authenticated POST /api/tickets admission only; excludes Worker execution"
        ),
        "response_records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=REPOSITORY_ROOT / "resources/evaluation/golden_dataset.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.concurrency < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--concurrency and --limit must be positive")
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")

    artifact = asyncio.run(run_benchmark(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote admission benchmark: {args.output}")
    print(
        f"accepted_202={artifact['accepted_202']}/{artifact['requested']} "
        f"http_p95_ms={artifact['http_response_latency_p95_ms_nearest_rank']} "
        f"worker_execution_measured={artifact['worker_execution_measured']}"
    )
    return 0 if artifact["http_response_p95_complete_sample"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
