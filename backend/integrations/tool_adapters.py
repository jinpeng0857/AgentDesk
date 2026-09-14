"""Concrete implementations of the eight Tool Protocol adapters.

Four read adapters query PostgreSQL. Four write adapters fail closed: phase 1
ships no write capability, and these classes contain no mutation code at all, so
there is nothing to accidentally enable by flipping a flag.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agents.policy import WriteCapabilityDisabled
from backend.db.models import OrderRecord, ServiceStatusRecord, UserRecord
from tools.schemas import (
    AccountStatus,
    CheckServiceArgs,
    CheckServiceResult,
    CreateTicketArgs,
    CreateTicketResult,
    OrderStatus,
    ProcessRefundArgs,
    ProcessRefundResult,
    QueryOrderArgs,
    QueryOrderResult,
    QueryUserArgs,
    QueryUserResult,
    RequestAccessArgs,
    RequestAccessResult,
    ResetPasswordArgs,
    ResetPasswordResult,
    ServiceStatus,
)

__all__ = [
    "DisabledAccessRequestService",
    "DisabledCredentialService",
    "DisabledRefundService",
    "DisabledTicketService",
    "SqlOrderRepository",
    "SqlServiceRepository",
    "SqlUserRepository",
    "WriteCapabilityDisabled",
]

# Accounts carry no version column until account mutation exists in the write
# phase, so the read model reports a stable version of 1.
STATIC_ACCOUNT_VERSION = 1


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query_user(self, args: QueryUserArgs) -> QueryUserResult:
        record = await self._session.get(UserRecord, args.user_id)
        if record is None:
            raise LookupError(f"user {args.user_id} does not exist")
        return QueryUserResult(
            user_id=record.id,
            account_status=(
                AccountStatus.ACTIVE if record.active else AccountStatus.INACTIVE
            ),
            masked_email=record.masked_email,
            version=STATIC_ACCOUNT_VERSION,
        )


class SqlOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query_order(self, args: QueryOrderArgs) -> QueryOrderResult:
        record = await self._session.get(OrderRecord, args.order_id)
        if record is None:
            raise LookupError(f"order {args.order_id} does not exist")
        return QueryOrderResult(
            order_id=record.id,
            requester_id=record.requester_id,
            status=OrderStatus(record.status),
            refundable_amount=record.refundable_amount,
            currency="USD",
            version=record.version,
        )


class SqlServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check_service(self, args: CheckServiceArgs) -> CheckServiceResult:
        record = await self._session.get(ServiceStatusRecord, args.service_name)
        if record is None:
            # Reporting an unknown service as operational would be a fabrication.
            raise LookupError(f"service {args.service_name} is not tracked")
        return CheckServiceResult(
            service_name=record.service_name,
            status=ServiceStatus(record.status),
            incident_id=record.incident_id,
            checked_at=record.checked_at or datetime.now(UTC),
        )


class _DisabledWriteCapability:
    """Shared fail-closed behaviour; subclasses add only the Protocol method."""

    tool_name = "unknown"

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    def _refuse(self) -> None:
        raise WriteCapabilityDisabled(
            f"{self.tool_name} has no phase 1 implementation and must not execute"
        )


class DisabledCredentialService(_DisabledWriteCapability):
    tool_name = "reset_password"

    async def reset_password(self, args: ResetPasswordArgs) -> ResetPasswordResult:
        del args
        self._refuse()
        raise AssertionError("unreachable")


class DisabledRefundService(_DisabledWriteCapability):
    tool_name = "process_refund"

    async def process_refund(self, args: ProcessRefundArgs) -> ProcessRefundResult:
        del args
        self._refuse()
        raise AssertionError("unreachable")


class DisabledTicketService(_DisabledWriteCapability):
    tool_name = "create_ticket"

    async def create_ticket(self, args: CreateTicketArgs) -> CreateTicketResult:
        del args
        self._refuse()
        raise AssertionError("unreachable")


class DisabledAccessRequestService(_DisabledWriteCapability):
    tool_name = "request_access"

    async def request_access(self, args: RequestAccessArgs) -> RequestAccessResult:
        del args
        self._refuse()
        raise AssertionError("unreachable")
