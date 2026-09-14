"""Ticket jobs executed by the ARQ worker."""

from __future__ import annotations

from typing import Any

from backend.jobs.context import WorkerContext

START_TICKET_JOB = "start_ticket"


def start_job_id(ticket_id: str) -> str:
    """Deterministic job ID: repeat delivery cannot create a second logical run."""
    return f"start:{ticket_id}"


def worker_context(ctx: dict[str, Any]) -> WorkerContext:
    context = ctx.get("worker")
    if not isinstance(context, WorkerContext):
        raise RuntimeError("worker context has not been initialised")
    return context


async def start_ticket(ctx: dict[str, Any], ticket_id: str) -> None:
    await worker_context(ctx).runner.run(ticket_id)
