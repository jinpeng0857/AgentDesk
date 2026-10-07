"""Full stack end-to-end smoke inspection script.

Verifies the complete system chain:
  Frontend / Reverse Proxy -> FastAPI -> Auth -> Ticket Creation (202) -> Eval Runs API.
Can run against Docker Compose (http://localhost:80) or local dev server (http://127.0.0.1:8000).
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEMO_USER = "requester"
DEMO_PASS = "requester-pass"
TERMINAL_STATUSES = {"resolved", "escalated", "rejected", "failed", "expired"}


def check_step(name: str, passed: bool, detail: str = "") -> None:
    status = "✓ PASS" if passed else "✕ FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" - {detail}"
    print(msg)
    if not passed:
        sys.exit(1)


def run_smoke(base_url: str, completion_timeout_s: float) -> None:
    print("=== AgentDesk Full-Stack Smoke Check ===")
    print(f"Target URL: {base_url}\n")
    start_time = time.monotonic()

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        # 1. Health check
        try:
            res = client.get("/health")
            passed = res.status_code == 200 and res.json().get("status") == "ok"
            check_step("GET /health", passed, f"status={res.status_code} body={res.text.strip()}")
        except Exception as exc:
            check_step("GET /health", False, str(exc))

        # 2. Ready check
        try:
            res = client.get("/ready")
            ready = res.status_code == 200 and res.json().get("status") == "ok"
            check_step(
                "GET /ready (PostgreSQL + Redis)",
                ready,
                f"status={res.status_code} body={res.text.strip()}",
            )
        except Exception as exc:
            check_step("GET /ready", False, str(exc))

        # 3. Auth login
        try:
            login_res = client.post(
                "/api/auth/login",
                json={"username": DEMO_USER, "password": DEMO_PASS},
            )
            token = login_res.json().get("access_token", "")
            check_step(
                "POST /api/auth/login",
                login_res.status_code == 200 and bool(token),
                f"status={login_res.status_code} token_present={bool(token)}",
            )
        except Exception as exc:
            check_step("POST /api/auth/login", False, str(exc))

        # 4. Authenticated request if token obtained
        headers = {"Authorization": f"Bearer {token}"}

        # 5. Create Ticket (Async 202 Contract check)
        try:
            ticket_res = client.post(
                "/api/tickets",
                json={"user_query": "Smoke test: network latency spike"},
                headers=headers,
            )
            ticket_data = ticket_res.json() if ticket_res.status_code == 202 else {}
            ticket_id = ticket_data.get("ticket_id")
            check_step(
                "POST /api/tickets (202 Accepted)",
                ticket_res.status_code == 202 and bool(ticket_id),
                f"status={ticket_res.status_code} ticket_id={ticket_id}",
            )
        except Exception as exc:
            check_step("POST /api/tickets", False, str(exc))

        # 6. Poll the persisted ticket projection until the asynchronous Worker finishes.
        try:
            deadline = time.monotonic() + completion_timeout_s
            ticket_detail = None
            while time.monotonic() < deadline:
                poll = client.get(f"/api/tickets/{ticket_id}", headers=headers)
                if poll.status_code != 200:
                    check_step(
                        "GET /api/tickets/{ticket_id}",
                        False,
                        f"status={poll.status_code} body={poll.text.strip()}",
                    )
                ticket_detail = poll.json()
                status = ticket_detail.get("status")
                if status in TERMINAL_STATUSES:
                    break
                time.sleep(1.0)

            status = ticket_detail.get("status") if isinstance(ticket_detail, dict) else None
            check_step(
                "Worker completes asynchronous ticket",
                status == "resolved",
                f"status={status}; timeout={completion_timeout_s:.0f}s",
            )
        except Exception as exc:
            check_step("Worker completes asynchronous ticket", False, str(exc))

        # 7. Eval Runs endpoint check
        try:
            eval_res = client.get("/api/eval/runs")
            runs = eval_res.json() if eval_res.status_code == 200 else None
            check_step(
                "GET /api/eval/runs",
                eval_res.status_code == 200 and isinstance(runs, list),
                f"status={eval_res.status_code} "
                + (
                    f"returned {len(runs)} recorded runs"
                    if isinstance(runs, list)
                    else f"body={eval_res.text.strip()}"
                ),
            )
        except Exception as exc:
            check_step("GET /api/eval/runs", False, str(exc))

    elapsed = time.monotonic() - start_time
    print(f"\n✓ Smoke check completed in {elapsed:.2f}s without fatal violations.")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentDesk Full-Stack Smoke Check")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="Base URL to test against")
    parser.add_argument(
        "--completion-timeout-s",
        type=float,
        default=300.0,
        help="Maximum time to wait for the Worker to resolve the submitted ticket",
    )
    args = parser.parse_args()
    if args.completion_timeout_s <= 0:
        parser.error("--completion-timeout-s must be positive")

    run_smoke(args.url, args.completion_timeout_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
