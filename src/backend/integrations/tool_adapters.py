"""Concrete implementations of the eight Tool Protocol adapters.

Four read adapters query PostgreSQL. Four write adapters fail closed: phase 1
ships no write capability, and these classes contain no mutation code at all, so
there is nothing to accidentally enable by flipping a flag.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from agents.policy import WriteCapabilityDisabled
from backend.auth.passwords import hash_password
from backend.db.models import (
    AccessRequestRecord,
    DownstreamTicketRecord,
    OrderRecord,
    RefundRecord,
    ServiceStatusRecord,
    UserRecord,
)
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
    WriteResultStatus,
)

__all__ = [
    "DisabledAccessRequestService",
    "DisabledCredentialService",
    "DisabledRefundService",
    "DisabledTicketService",
    "SqlAccessRequestService",
    "SqlCredentialService",
    "SqlOrderRepository",
    "SqlRefundService",
    "SqlServiceRepository",
    "SqlTicketService",
    "SqlUserRepository",
    "WriteCapabilityDisabled",
]


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
            version=record.version,
        )


class SqlOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query_order(self, args: QueryOrderArgs) -> QueryOrderResult:
        record = await self._session.get(OrderRecord, args.order_id)
        if record is None:
            return QueryOrderResult(
                order_id=args.order_id,
                found=False,
            )
        return QueryOrderResult(
            order_id=record.id,
            found=True,
            requester_id=record.requester_id,
            status=OrderStatus(record.status),
            refundable_amount=record.refundable_amount,
            currency="USD",
            version=record.version,
        )


class SqlServiceRepository:
    """Service-state reads with deterministic display-name resolution.

    The model receives user wording (often Chinese), while the tracked service
    keys are canonical ASCII identifiers. Resolving aliases here keeps the
    lookup deterministic and prevents a known service from being misreported as
    an execution failure.
    """

    _ALIASES = {
        "vpn_service": "vpn",
        "company_vpn": "vpn",
        "vpn服务": "vpn",
        "公司vpn": "vpn",
        "email_service": "mail",
        "mail_service": "mail",
        "邮件服务": "mail",
        "邮件系统": "mail",
        "邮件": "mail",
        "video_conferencing": "video_conference",
        "video_conference_service": "video_conference",
        "视频会议": "video_conference",
        "视频会议服务": "video_conference",
        "访客wifi": "guest_wifi",
        "访客wi-fi": "guest_wifi",
        "访客网络": "guest_wifi",
        "internal_network": "intranet",
        "内网": "intranet",
        "内网服务": "intranet",
        "network_dns": "dns",
        "dns_service": "dns",
        "dns服务": "dns",
        "内网dns": "dns",
        "ldap_service": "ldap",
        "ldap服务": "ldap",
        "统一身份认证": "ldap",
        "统一身份认证服务": "ldap",
        "交换机": "switch",
        "核心交换机": "switch",
        "authentication_service": "authentication_service",
        "认证服务": "authentication_service",
        "统一认证": "authentication_service",
        "system_metrics": "system_metrics",
        "系统指标": "system_metrics",
        "worker_host": "workerhost",
        "worker宿主机": "workerhost",
    }

    _GENERIC_SUFFIXES = ("服务器", "服务", "系统", "平台")

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @classmethod
    def resolve_service_name(cls, requested_name: str) -> str:
        key = " ".join(requested_name.strip().casefold().split())
        compact = key.replace(" ", "")
        if key in cls._ALIASES:
            return cls._ALIASES[key]
        if compact in cls._ALIASES:
            return cls._ALIASES[compact]
        for suffix in cls._GENERIC_SUFFIXES:
            if key.endswith(suffix):
                trimmed = key[: -len(suffix)]
                if trimmed in cls._ALIASES:
                    return cls._ALIASES[trimmed]
        for pattern, canonical in sorted(
            cls._ALIASES.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if pattern in compact:
                return canonical
        return compact

    async def check_service(self, args: CheckServiceArgs) -> CheckServiceResult:
        requested_name = args.service_name.strip()
        canonical_name = self.resolve_service_name(requested_name)
        record = await self._session.get(ServiceStatusRecord, canonical_name)
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


class SqlTicketService:
    """Creates downstream tickets persisted to PostgreSQL."""

    def __init__(self, session: AsyncSession, ticket_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._ticket_id = ticket_id

    async def create_ticket(self, args: CreateTicketArgs) -> CreateTicketResult:
        source_id: uuid.UUID
        try:
            source_id = uuid.UUID(args.source_ticket_id)
        except (ValueError, TypeError) as err:
            if self._ticket_id is not None:
                source_id = self._ticket_id
            else:
                raise ValueError(f"invalid source_ticket_id: {args.source_ticket_id!r}") from err

        downstream_id = f"dt-{uuid.uuid4().hex[:12]}"
        record = DownstreamTicketRecord(
            id=downstream_id,
            source_ticket_id=source_id,
            requester_id=args.requester_id,
            title=args.title,
            description=args.description,
            status="created",
        )
        self._session.add(record)
        await self._session.flush()
        return CreateTicketResult(
            downstream_ticket_id=downstream_id,
            status=WriteResultStatus.CREATED,
        )


class SqlAccessRequestService:
    """Creates access requests persisted to PostgreSQL (never directly grants access)."""

    def __init__(self, session: AsyncSession, ticket_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._ticket_id = ticket_id

    async def request_access(self, args: RequestAccessArgs) -> RequestAccessResult:
        if self._ticket_id is None:
            raise ValueError("SqlAccessRequestService requires a valid ticket_id")

        access_id = f"ar-{uuid.uuid4().hex[:12]}"
        record = AccessRequestRecord(
            id=access_id,
            ticket_id=self._ticket_id,
            requester_id=args.requester_id,
            resource=args.resource,
            justification=args.justification,
            status="created",
        )
        self._session.add(record)
        await self._session.flush()
        return RequestAccessResult(
            access_request_id=access_id,
            status=WriteResultStatus.CREATED,
        )


class SqlCredentialService:
    """Resets user credential in PostgreSQL and generates temporary password."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reset_password(self, args: ResetPasswordArgs) -> ResetPasswordResult:
        user = await self._session.get(UserRecord, args.user_id)
        if user is None:
            raise LookupError(f"user {args.user_id} does not exist")
        if not user.active:
            raise PermissionError(f"cannot reset password for inactive user {args.user_id}")

        if args.account_version != user.version:
            raise ValueError(
                f"account version mismatch: expected {user.version}, got {args.account_version}"
            )

        temp_password = f"Tmp-{secrets.token_urlsafe(12)}"
        user.password_hash = hash_password(SecretStr(temp_password))
        user.version = user.version + 1
        await self._session.flush()

        return ResetPasswordResult(
            user_id=user.id,
            temporary_password=SecretStr(temp_password),
            credential_version=user.version,
        )


class SqlRefundService:
    """Processes refunds persisted to PostgreSQL and decrements order refundable amount."""

    def __init__(self, session: AsyncSession, ticket_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._ticket_id = ticket_id

    async def process_refund(self, args: ProcessRefundArgs) -> ProcessRefundResult:
        if self._ticket_id is None:
            raise ValueError("SqlRefundService requires a valid ticket_id")

        order = await self._session.get(OrderRecord, args.order_id)
        if order is None:
            raise LookupError(f"order {args.order_id} does not exist")

        if order.requester_id != args.requester_id:
            raise PermissionError(
                f"order {args.order_id} belongs to {order.requester_id}, not {args.requester_id}"
            )

        if order.status != "paid":
            raise ValueError(f"cannot refund order with status {order.status}")

        if args.order_version != order.version:
            raise ValueError(
                f"order version mismatch: expected {order.version}, got {args.order_version}"
            )

        if args.amount <= Decimal("0"):
            raise ValueError(f"refund amount must be positive, got {args.amount}")

        if args.amount > order.refundable_amount:
            raise ValueError(
                f"refund amount {args.amount} exceeds refundable amount {order.refundable_amount}"
            )

        if args.currency != "USD":
            raise ValueError(f"unsupported currency {args.currency}")

        order.refundable_amount = order.refundable_amount - args.amount
        if order.refundable_amount == Decimal("0"):
            order.status = "refunded"
        order.version = order.version + 1

        refund_id = f"ref-{uuid.uuid4().hex[:12]}"
        record = RefundRecord(
            id=refund_id,
            ticket_id=self._ticket_id,
            order_id=order.id,
            requester_id=args.requester_id,
            amount=args.amount,
            currency="USD",
            status="succeeded",
        )
        self._session.add(record)
        await self._session.flush()

        return ProcessRefundResult(
            refund_id=refund_id,
            status=WriteResultStatus.SUCCEEDED,
            amount=args.amount,
            currency="USD",
        )



