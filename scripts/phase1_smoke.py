"""Automated Phase 1 end-to-end smoke verification.

Tests the live read-only vertical slice:
1. Logs in as requester with seeded credentials.
2. Submits a VPN troubleshooting ticket, asserting HTTP 202 status='queued'.
3. Polls until terminal status='resolved'.
4. Asserts presence of 5-field citations (document_id, chunk_id, title, excerpt, score).
5. Asserts trace events exist and contains no leaked credentials.
6. Verifies zero write tool executions.
7. Supports --assert-existing-ticket-queryable to verify persistence across worker restarts.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
TICKET_STATE_FILE = REPO_ROOT / ".phase1_smoke_ticket_id"

WRITE_TOOL_NAMES = {
    "create_ticket",
    "request_access",
    "reset_password",
    "process_refund",
}

SEEDED_REQUESTER_USER = "requester"
SEEDED_REQUESTER_PASS = "requester-pass"
VPN_QUERY = "公司 VPN 无法连接，请查询当前服务状态并给出恢复 SOP。"


def log(msg: str) -> None:
    print(f"[phase1-smoke] {msg}", flush=True)


def check_citations(ticket: dict[str, Any]) -> None:
    refs = ticket.get("knowledge_refs") or []
    if not refs:
        raise AssertionError("Expected at least one knowledge reference citation, got none")

    for ref in refs:
        missing = {"document_id", "chunk_id", "title", "content_excerpt", "score"} - set(ref.keys())
        if missing:
            raise AssertionError(f"Citation missing required fields {missing}: {ref}")
        if not (0.0 <= float(ref["score"]) <= 1.0):
            raise AssertionError(f"Citation score out of bounds [0, 1]: {ref['score']}")

    log(f"Verified {len(refs)} knowledge citations with complete 5-field schemas")


def check_trace_and_audit(client: httpx.Client, base_url: str, ticket_id: str) -> None:
    resp = client.get(f"{base_url}/api/tickets/{ticket_id}/trace")
    if resp.status_code != 200:
        raise AssertionError(f"Failed to fetch trace for {ticket_id}: {resp.status_code}")

    trace_events = resp.json()
    if not trace_events:
        raise AssertionError(f"Trace events empty for ticket {ticket_id}")

    raw_text = resp.text
    forbidden_leak_strings = [
        SEEDED_REQUESTER_PASS,
        "Bearer ",
        "token=",
        "password=",
    ]
    for forbidden in forbidden_leak_strings:
        if forbidden in raw_text:
            raise AssertionError(f"Sensitive credential leaked in trace: {forbidden}")

    # Assert zero write tool executions
    for event in trace_events:
        event_type = event.get("event_type", "")
        for write_tool in WRITE_TOOL_NAMES:
            if write_tool in event_type:
                raise AssertionError(f"Forbidden business write tool executed: {write_tool}")

    log(f"Verified trace with {len(trace_events)} events; zero write tool calls; no leaked secrets")


def run_smoke(base_url: str, timeout_s: float = 40.0) -> str:
    with httpx.Client(timeout=10.0) as client:
        # 1. Login
        log(f"Logging in as {SEEDED_REQUESTER_USER}...")
        login_resp = client.post(
            f"{base_url}/api/auth/login",
            json={"username": SEEDED_REQUESTER_USER, "password": SEEDED_REQUESTER_PASS},
        )
        if login_resp.status_code != 200:
            raise RuntimeError(f"Login failed: {login_resp.status_code} {login_resp.text}")

        token = login_resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        log("Login successful, received Bearer token")

        # 2. Submit VPN ticket
        log(f"Submitting ticket: {VPN_QUERY}")
        submit_resp = client.post(
            f"{base_url}/api/tickets",
            json={"user_query": VPN_QUERY},
        )
        if submit_resp.status_code != 202:
            raise RuntimeError(
                f"Expected 202 Accepted, got {submit_resp.status_code} {submit_resp.text}"
            )

        accepted_data = submit_resp.json()
        ticket_id = accepted_data["ticket_id"]
        status = accepted_data["status"]
        if status != "queued":
            raise AssertionError(f"Expected initial status 'queued', got '{status}'")
        log(f"Ticket accepted: id={ticket_id}, status={status}")

        # Save for later cross-restart query verification
        TICKET_STATE_FILE.write_text(ticket_id, encoding="utf-8")

        # 3. Poll until resolved
        log(f"Polling ticket {ticket_id} until terminal resolved status...")
        start_time = time.monotonic()
        ticket_detail: dict[str, Any] | None = None

        while time.monotonic() - start_time < timeout_s:
            poll_resp = client.get(f"{base_url}/api/tickets/{ticket_id}")
            if poll_resp.status_code != 200:
                raise RuntimeError(f"Poll failed: {poll_resp.status_code} {poll_resp.text}")

            ticket_detail = poll_resp.json()
            curr_status = ticket_detail["status"]
            log(f"  status: {curr_status} (elapsed: {time.monotonic() - start_time:.1f}s)")

            if curr_status == "resolved":
                break
            if curr_status in ("failed", "rejected", "escalated", "expired"):
                raise AssertionError(f"Ticket entered non-resolved terminal status: {curr_status}")

            time.sleep(1.5)

        if not ticket_detail or ticket_detail["status"] != "resolved":
            raise TimeoutError(f"Ticket did not reach 'resolved' within {timeout_s} seconds")

        log("Ticket successfully resolved!")
        log(f"Final response: {ticket_detail.get('final_response')}")

        # 4. Citations check
        check_citations(ticket_detail)

        # 5. Trace & Zero Write Side-effects check
        check_trace_and_audit(client, base_url, ticket_id)

        log("Phase 1 smoke test PASSED")
        return str(ticket_id)


def assert_existing_ticket_queryable(base_url: str) -> None:
    if not TICKET_STATE_FILE.exists():
        raise RuntimeError(f"No existing ticket state file found at {TICKET_STATE_FILE}")

    ticket_id = TICKET_STATE_FILE.read_text(encoding="utf-8").strip()
    log(f"Asserting existing ticket {ticket_id} is queryable across worker restart...")

    with httpx.Client(timeout=10.0) as client:
        login_resp = client.post(
            f"{base_url}/api/auth/login",
            json={"username": SEEDED_REQUESTER_USER, "password": SEEDED_REQUESTER_PASS},
        )
        if login_resp.status_code != 200:
            raise RuntimeError(f"Login failed: {login_resp.status_code} {login_resp.text}")

        token = login_resp.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        detail_resp = client.get(f"{base_url}/api/tickets/{ticket_id}")
        if detail_resp.status_code != 200:
            raise AssertionError(
                f"Existing ticket {ticket_id} not found: {detail_resp.status_code}"
            )

        detail = detail_resp.json()
        if detail["status"] != "resolved":
            raise AssertionError(f"Expected status 'resolved', got '{detail['status']}'")

        check_citations(detail)
        check_trace_and_audit(client, base_url, ticket_id)
        log(f"Existing ticket {ticket_id} verified successfully after worker restart!")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 read-only vertical slice smoke test")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="AgentDesk API base URL"
    )
    parser.add_argument("--timeout", type=float, default=40.0, help="Polling timeout in seconds")
    parser.add_argument(
        "--assert-existing-ticket-queryable",
        action="store_true",
        help="Only verify that a previously resolved ticket remains queryable and intact",
    )
    args = parser.parse_args()

    try:
        if args.assert_existing_ticket_queryable:
            assert_existing_ticket_queryable(args.base_url)
        else:
            run_smoke(args.base_url, timeout_s=args.timeout)
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
