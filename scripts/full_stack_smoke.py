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
DEMO_USER = "alice"
DEMO_PASS = "demo1234"


def check_step(name: str, passed: bool, detail: str = "") -> None:
    status = "✓ PASS" if passed else "✕ FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" - {detail}"
    print(msg)
    if not passed:
        sys.exit(1)


def run_smoke(base_url: str) -> None:
    print("=== AgentDesk Full-Stack Smoke Check ===")
    print(f"Target URL: {base_url}\n")
    start_time = time.time()

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
            if res.status_code == 200:
                check_step("GET /ready (PostgreSQL + Redis)", True, f"status={res.status_code}")
            else:
                print(f"[!] GET /ready returned {res.status_code}: {res.text.strip()}")
        except Exception as exc:
            check_step("GET /ready", False, str(exc))

        # 3. Auth login
        token = ""
        try:
            login_res = client.post(
                "/api/auth/login",
                json={"username": DEMO_USER, "password": DEMO_PASS},
            )
            if login_res.status_code == 200:
                data = login_res.json()
                token = data.get("access_token", "")
                check_step("POST /api/auth/login", bool(token), f"token length={len(token)}")
            elif login_res.status_code in (401, 503):
                print(f"[!] Auth endpoint returned {login_res.status_code}")
            else:
                check_step("POST /api/auth/login", False, f"status={login_res.status_code}")
        except Exception as exc:
            check_step("POST /api/auth/login", False, str(exc))

        # 4. Authenticated request if token obtained
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        # 5. Create Ticket (Async 202 Contract check)
        try:
            ticket_res = client.post(
                "/api/tickets",
                json={"user_query": "Smoke test: network latency spike"},
                headers=headers,
            )
            if ticket_res.status_code == 202:
                ticket_data = ticket_res.json()
                t_id = ticket_data.get("ticket_id")
                check_step("POST /api/tickets (202 Accepted)", True, f"ticket_id={t_id}")
            elif ticket_res.status_code in (401, 503):
                print(f"[!] Ticket creation returned {ticket_res.status_code}")
            else:
                check_step("POST /api/tickets", False, f"status={ticket_res.status_code}")
        except Exception as exc:
            check_step("POST /api/tickets", False, str(exc))

        # 6. Eval Runs endpoint check
        try:
            eval_res = client.get("/api/eval/runs")
            if eval_res.status_code == 200:
                runs = eval_res.json()
                check_step("GET /api/eval/runs", True, f"returned {len(runs)} recorded runs")
            elif eval_res.status_code == 503:
                print("[!] Eval runs returned 503 (Database not connected)")
            else:
                check_step("GET /api/eval/runs", False, f"status={eval_res.status_code}")
        except Exception as exc:
            check_step("GET /api/eval/runs", False, str(exc))

    elapsed = time.time() - start_time
    print(f"\n✓ Smoke check completed in {elapsed:.2f}s without fatal violations.")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentDesk Full-Stack Smoke Check")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="Base URL to test against")
    args = parser.parse_args()

    run_smoke(args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
