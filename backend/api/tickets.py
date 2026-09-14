"""Ticket API routes.

``POST /api/tickets`` writes the ticket and its outbox row in one transaction and
returns HTTP 202. This module must never start or resume the graph.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status

from agents.schemas import TicketStatus
from backend.api.dependencies import SessionDep
from backend.api.schemas import (
    ErrorEnvelope,
    TicketAcceptedResponse,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketSummaryResponse,
    TraceEventResponse,
)
from backend.api.stubs import STUB_RESPONSE, raise_not_implemented
from backend.auth.dependencies import CurrentActor
from backend.db.models import TicketRecord
from backend.db.repositories.outbox import OutboxRepository
from backend.jobs.tickets import start_job_id

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

SUBMISSION_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    503: {"model": ErrorEnvelope, "description": "Runtime dependencies are not ready"},
}


@router.post(
    "",
    response_model=TicketAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=SUBMISSION_RESPONSES,
)
async def create_ticket(
    payload: TicketCreateRequest,
    actor: CurrentActor,
    session: SessionDep,
) -> TicketAcceptedResponse:
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    session.add(
        TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id=actor.user_id,
            user_query=payload.user_query,
            trace_id=trace_id,
            status=str(TicketStatus.QUEUED),
            step_count=0,
        )
    )
    # Flush the ticket before the outbox row so the foreign key resolves inside
    # the same transaction; both still commit or roll back together.
    await session.flush()
    await OutboxRepository(session).enqueue_start_ticket(
        ticket_id=ticket_id,
        dedupe_key=start_job_id(str(ticket_id)),
    )
    await session.commit()

    return TicketAcceptedResponse(
        ticket_id=str(ticket_id),
        trace_id=str(trace_id),
        status=TicketStatus.QUEUED,
    )


@router.get("", response_model=list[TicketSummaryResponse], responses=STUB_RESPONSE)
async def list_tickets() -> list[TicketSummaryResponse]:
    raise_not_implemented()


@router.get(
    "/{ticket_id}",
    response_model=TicketDetailResponse,
    responses=STUB_RESPONSE,
)
async def get_ticket(ticket_id: str) -> TicketDetailResponse:
    del ticket_id
    raise_not_implemented()


@router.get(
    "/{ticket_id}/trace",
    response_model=list[TraceEventResponse],
    responses=STUB_RESPONSE,
)
async def get_ticket_trace(ticket_id: str) -> list[TraceEventResponse]:
    del ticket_id
    raise_not_implemented()
