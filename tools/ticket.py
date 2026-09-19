"""Ticket-domain read capability: the order data attached to a ticket.

Domain layer: this module depends only on ``tools.context`` Protocols and must
never import ``backend``, a database driver, Redis or ARQ. The four write
capabilities (``create_ticket``, ``request_access``, ``reset_password``,
``process_refund``) have no phase 1 implementation by design.
"""

from __future__ import annotations

from tools.context import OrderRepository, TicketService
from tools.schemas import (
    CreateTicketArgs,
    CreateTicketResult,
    QueryOrderArgs,
    QueryOrderResult,
)


async def query_order(
    args: QueryOrderArgs, repository: OrderRepository
) -> QueryOrderResult:
    return await repository.query_order(args)


async def create_ticket(
    args: CreateTicketArgs, service: TicketService
) -> CreateTicketResult:
    return await service.create_ticket(args)

