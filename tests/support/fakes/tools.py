"""Deterministic fakes for the eight domain Protocols and the audit sink."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from backend.governance.audit import ToolAttempt
from backend.integrations.tool_adapters import WriteCapabilityDisabled
from tools.schemas import (
    AccountStatus,
    CheckServiceArgs,
    CheckServiceResult,
    CreateTicketArgs,
    CreateTicketResult,
    KnowledgeSearchItem,
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
    SearchKnowledgeArgs,
    SearchKnowledgeResult,
    ServiceStatus,
)


def _refuse(tool_name: str) -> None:
    raise WriteCapabilityDisabled(f"{tool_name} has no phase 1 implementation")


@dataclass
class RecordingKnowledgeStore:
    result: SearchKnowledgeResult
    calls: list[SearchKnowledgeArgs] = field(default_factory=list)

    async def search(self, args: SearchKnowledgeArgs) -> SearchKnowledgeResult:
        self.calls.append(args)
        return self.result


@dataclass
class RecordingUserRepository:
    result: QueryUserResult
    calls: list[QueryUserArgs] = field(default_factory=list)

    async def query_user(self, args: QueryUserArgs) -> QueryUserResult:
        self.calls.append(args)
        return self.result


@dataclass
class RecordingOrderRepository:
    result: QueryOrderResult
    calls: list[QueryOrderArgs] = field(default_factory=list)

    async def query_order(self, args: QueryOrderArgs) -> QueryOrderResult:
        self.calls.append(args)
        return self.result


@dataclass
class RecordingServiceRepository:
    result: CheckServiceResult
    calls: list[CheckServiceArgs] = field(default_factory=list)
    error: Exception | None = None
    delay_s: float = 0.0

    async def check_service(self, args: CheckServiceArgs) -> CheckServiceResult:
        self.calls.append(args)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class DisabledCredentialService:
    calls: list[ResetPasswordArgs] = field(default_factory=list)

    async def reset_password(self, args: ResetPasswordArgs) -> ResetPasswordResult:
        self.calls.append(args)
        _refuse("reset_password")
        raise AssertionError("unreachable")


@dataclass
class DisabledRefundService:
    calls: list[ProcessRefundArgs] = field(default_factory=list)

    async def process_refund(self, args: ProcessRefundArgs) -> ProcessRefundResult:
        self.calls.append(args)
        _refuse("process_refund")
        raise AssertionError("unreachable")


@dataclass
class DisabledTicketService:
    calls: list[CreateTicketArgs] = field(default_factory=list)

    async def create_ticket(self, args: CreateTicketArgs) -> CreateTicketResult:
        self.calls.append(args)
        _refuse("create_ticket")
        raise AssertionError("unreachable")


@dataclass
class DisabledAccessRequestService:
    calls: list[RequestAccessArgs] = field(default_factory=list)

    async def request_access(self, args: RequestAccessArgs) -> RequestAccessResult:
        self.calls.append(args)
        _refuse("request_access")
        raise AssertionError("unreachable")


@dataclass
class InMemoryAuditSink:
    attempts: list[ToolAttempt] = field(default_factory=list)

    async def record(self, attempt: ToolAttempt) -> None:
        self.attempts.append(attempt)

    @property
    def outcomes(self) -> list[str]:
        return [str(attempt.outcome) for attempt in self.attempts]


def knowledge_result(
    document_id: str = "DOC-VPN-001", chunk_id: str = "chunk-1"
) -> SearchKnowledgeResult:
    return SearchKnowledgeResult(
        refs=[
            KnowledgeSearchItem(
                document_id=document_id,
                chunk_id=chunk_id,
                title="VPN connectivity troubleshooting",
                content_excerpt="Verify the client version, then check the service status page.",
                score=0.87,
            )
        ]
    )


def service_result(status: ServiceStatus = ServiceStatus.DEGRADED) -> CheckServiceResult:
    return CheckServiceResult(
        service_name="vpn",
        status=status,
        incident_id="INC-1001",
        checked_at=datetime.now(UTC),
    )


def user_result(user_id: str = "user-requester-001") -> QueryUserResult:
    return QueryUserResult(
        user_id=user_id,
        account_status=AccountStatus.ACTIVE,
        masked_email="r***@example.com",
        version=3,
    )


def order_result(order_id: str = "ORD-1001") -> QueryOrderResult:
    return QueryOrderResult(
        found=True,
        order_id=order_id,
        requester_id="user-requester-001",
        status=OrderStatus.PAID,
        refundable_amount=Decimal("50.00"),
        currency="USD",
        version=2,
    )
