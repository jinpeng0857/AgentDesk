"""Ticket API contract routes."""

from fastapi import APIRouter, status

from backend.api.schemas import (
    TicketAcceptedResponse,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketSummaryResponse,
    TraceEventResponse,
)
from backend.api.stubs import STUB_RESPONSE, raise_not_implemented

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.post(
    "",
    response_model=TicketAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=STUB_RESPONSE,
)
async def create_ticket(_: TicketCreateRequest) -> TicketAcceptedResponse:
    raise_not_implemented()


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
