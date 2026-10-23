"""Worker job boundary behavior for retryable model transport failures."""

from __future__ import annotations

from typing import cast

import pytest
from arq import Retry
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import TicketStatus
from backend.config import AppSettings
from backend.db.repositories.approvals import ApprovalConflictError
from backend.integrations.model_client import ModelTransportError, ModelTransportFailureKind
from backend.jobs.context import WorkerContext
from backend.jobs.tickets import start_ticket


class TransportFailingRunner:
    def __init__(self, error: Exception | None = None) -> None:
        self.failures: list[tuple[str, str]] = []
        self.error = error or ModelTransportError("provider temporarily unavailable")

    async def run(self, ticket_id: str) -> TicketStatus:
        raise self.error

    async def resume(self, ticket_id: str, approval_id: str) -> TicketStatus:
        raise ModelTransportError("provider temporarily unavailable")

    async def fail(self, ticket_id: str, failure_code: str) -> TicketStatus:
        self.failures.append((ticket_id, failure_code))
        return TicketStatus.ESCALATED


def worker_context(runner: TransportFailingRunner) -> dict[str, object]:
    return {
        "worker": WorkerContext(
            settings=AppSettings(openai_model="test-model"),
            session_factory=cast(async_sessionmaker[AsyncSession], None),
            redis=cast(object, None),  # type: ignore[arg-type]
            runner=cast(object, runner),  # type: ignore[arg-type]
        )
    }


async def test_start_ticket_requests_arq_retry_for_transient_model_transport() -> None:
    runner = TransportFailingRunner()
    context = worker_context(runner)
    context["job_try"] = 1

    with pytest.raises(Retry) as retry:
        await start_ticket(context, "ticket-123")

    assert retry.value.defer_score is not None
    assert retry.value.defer_score > 0
    assert runner.failures == []


async def test_start_ticket_escalates_after_transport_retry_budget_is_exhausted() -> None:
    runner = TransportFailingRunner(
        ModelTransportError(
            "model provider timed out",
            kind=ModelTransportFailureKind.TIMEOUT,
        )
    )
    context = worker_context(runner)
    context["job_try"] = 3

    status = await start_ticket(context, "ticket-123")

    assert status is TicketStatus.ESCALATED
    assert runner.failures == [("ticket-123", "model_timeout_exhausted")]


@pytest.mark.parametrize(
    ("kind", "status_code", "expected_code"),
    [
        (ModelTransportFailureKind.CONNECTION, None, "model_connection_exhausted"),
        (ModelTransportFailureKind.SERVER_ERROR, 503, "model_server_error_503_exhausted"),
    ],
)
async def test_start_ticket_records_transport_failure_category_after_retries(
    kind: ModelTransportFailureKind,
    status_code: int | None,
    expected_code: str,
) -> None:
    runner = TransportFailingRunner(
        ModelTransportError("classified model failure", kind=kind, status_code=status_code)
    )
    context = worker_context(runner)
    context["job_try"] = 3

    status = await start_ticket(context, "ticket-123")

    assert status is TicketStatus.ESCALATED
    assert runner.failures == [("ticket-123", expected_code)]


async def test_start_ticket_retries_transient_database_failure() -> None:
    error = DBAPIError("select 1", {}, RuntimeError("connection reset"), False)
    runner = TransportFailingRunner(error)
    context = worker_context(runner)
    context["job_try"] = 1

    with pytest.raises(Retry):
        await start_ticket(context, "ticket-123")

    assert runner.failures == []


async def test_start_ticket_preserves_approval_conflict() -> None:
    conflict = ApprovalConflictError("approval version conflict")
    runner = TransportFailingRunner(conflict)
    context = worker_context(runner)

    with pytest.raises(ApprovalConflictError, match="approval version conflict"):
        await start_ticket(context, "ticket-123")

    assert runner.failures == []
