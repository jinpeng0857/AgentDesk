"""Infrastructure-neutral dependencies used by Tool implementations."""

from __future__ import annotations

from typing import Protocol

from tools.schemas import (
    CheckServiceArgs,
    CheckServiceResult,
    CreateTicketArgs,
    CreateTicketResult,
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
)


class KnowledgeStore(Protocol):
    async def search(self, args: SearchKnowledgeArgs) -> SearchKnowledgeResult: ...


class UserRepository(Protocol):
    async def query_user(self, args: QueryUserArgs) -> QueryUserResult: ...


class OrderRepository(Protocol):
    async def query_order(self, args: QueryOrderArgs) -> QueryOrderResult: ...


class ServiceRepository(Protocol):
    async def check_service(self, args: CheckServiceArgs) -> CheckServiceResult: ...


class CredentialService(Protocol):
    async def reset_password(self, args: ResetPasswordArgs) -> ResetPasswordResult: ...


class RefundService(Protocol):
    async def process_refund(self, args: ProcessRefundArgs) -> ProcessRefundResult: ...


class TicketService(Protocol):
    async def create_ticket(self, args: CreateTicketArgs) -> CreateTicketResult: ...


class AccessRequestService(Protocol):
    async def request_access(self, args: RequestAccessArgs) -> RequestAccessResult: ...
