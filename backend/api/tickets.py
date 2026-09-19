"""Ticket API routes.

``POST /api/tickets`` writes the ticket and its outbox row in one transaction and
returns HTTP 202. This module must never start or resume the graph.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter, status

from agents.schemas import Intent, RiskLevel, TicketStatus
from backend.api.dependencies import SessionDep
from backend.api.errors import ApiError
from backend.api.schemas import (
    ErrorEnvelope,
    KnowledgeRefResponse,
    TicketAcceptedResponse,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketSummaryResponse,
    TraceEventResponse,
)
from backend.auth.dependencies import CurrentActor
from backend.db.models import TicketRecord
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.outbox import OutboxRepository
from backend.db.repositories.tickets import TicketRepository
from backend.jobs.tickets import start_job_id

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

SUBMISSION_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    503: {"model": ErrorEnvelope, "description": "Runtime dependencies are not ready"},
}

QUERY_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    404: {"model": ErrorEnvelope, "description": "Ticket not found"},
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


@router.get(
    "",
    response_model=list[TicketSummaryResponse],
    responses={401: {"model": ErrorEnvelope, "description": "Authentication failed"}},
)
async def list_tickets(
    actor: CurrentActor,
    session: SessionDep,
) -> list[TicketSummaryResponse]:
    repo = TicketRepository(session)
    records = await repo.list_for_requester(actor.user_id)
    return [
        TicketSummaryResponse(
            ticket_id=str(record.id),
            status=TicketStatus(record.status),
            intent=Intent(record.intent) if record.intent else None,
            risk_level=RiskLevel(record.risk_level) if record.risk_level else None,
            updated_at=record.updated_at,
        )
        for record in records
    ]


@router.get(
    "/{ticket_id}",
    response_model=TicketDetailResponse,
    responses=QUERY_RESPONSES,
)
async def get_ticket(
    ticket_id: str,
    actor: CurrentActor,
    session: SessionDep,
) -> TicketDetailResponse:
    try:
        ticket_uuid = uuid.UUID(ticket_id)
    except ValueError:
        raise ApiError(
            status_code=404,
            code="TICKET_NOT_FOUND",
            message="Ticket not found",
        ) from None

    repo = TicketRepository(session)
    record = await repo.get_for_requester(ticket_uuid, actor.user_id)
    if record is None:
        raise ApiError(status_code=404, code="TICKET_NOT_FOUND", message="Ticket not found")

    refs = await repo.list_knowledge_refs(ticket_uuid)
    return TicketDetailResponse(
        ticket_id=str(record.id),
        requester_id=record.requester_id,
        status=TicketStatus(record.status),
        intent=Intent(record.intent) if record.intent else None,
        risk_level=RiskLevel(record.risk_level) if record.risk_level else None,
        resolution_mode=cast(Any, record.resolution_mode),
        final_response=record.final_response,
        knowledge_refs=[
            KnowledgeRefResponse(
                document_id=ref.document_id,
                chunk_id=ref.chunk_id,
                title=ref.title,
                content_excerpt=ref.content_excerpt,
                score=ref.score,
            )
            for ref in refs
        ],
        updated_at=record.updated_at,
    )


@router.get(
    "/{ticket_id}/trace",
    response_model=list[TraceEventResponse],
    responses=QUERY_RESPONSES,
)
async def get_ticket_trace(
    ticket_id: str,
    actor: CurrentActor,
    session: SessionDep,
) -> list[TraceEventResponse]:
    try:
        ticket_uuid = uuid.UUID(ticket_id)
    except ValueError:
        raise ApiError(
            status_code=404,
            code="TICKET_NOT_FOUND",
            message="Ticket not found",
        ) from None

    repo = TicketRepository(session)
    record = await repo.get_for_requester(ticket_uuid, actor.user_id)
    if record is None:
        raise ApiError(status_code=404, code="TICKET_NOT_FOUND", message="Ticket not found")

    trace_records = await repo.list_trace_events(ticket_uuid)
    audit_repo = AuditRepository(session)
    tool_call_records = await audit_repo.list_for_ticket(ticket_uuid)

    events: list[TraceEventResponse] = []
    for tr in trace_records:
        events.append(
            TraceEventResponse(
                request_id=tr.request_id,
                ticket_id=str(tr.ticket_id),
                trace_id=str(tr.trace_id),
                actor=cast(Any, tr.actor),
                event_type=tr.event_type,
                summary=tr.summary,
                timestamp=tr.timestamp,
            )
        )

    for tc in tool_call_records:
        events.append(
            TraceEventResponse(
                request_id=tc.request_id,
                ticket_id=str(tc.ticket_id),
                trace_id=str(tc.trace_id),
                actor=cast(Any, tc.agent_id),
                event_type=f"tool_call:{tc.tool_name}",
                summary=f"tool={tc.tool_name} outcome={tc.outcome} latency_ms={tc.latency_ms}",
                timestamp=tc.timestamp,
            )
        )

    events.sort(key=lambda e: (e.timestamp, e.request_id))
    return events
