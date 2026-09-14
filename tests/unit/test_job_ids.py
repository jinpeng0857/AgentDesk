"""Job IDs must be deterministic so duplicate delivery cannot double-run a ticket."""

from __future__ import annotations

import uuid

from backend.jobs.tickets import start_job_id


def test_start_job_id_is_deterministic() -> None:
    assert start_job_id("ticket-123") == "start:ticket-123"


def test_start_job_id_is_stable_for_the_same_uuid() -> None:
    ticket_id = str(uuid.UUID("11111111-2222-3333-4444-555555555555"))

    assert start_job_id(ticket_id) == start_job_id(ticket_id)
    assert start_job_id(ticket_id) == f"start:{ticket_id}"


def test_start_job_id_differs_per_ticket() -> None:
    assert start_job_id("ticket-a") != start_job_id("ticket-b")
