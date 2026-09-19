"""Unit tests for create_ticket and request_access domain tools and gateway integration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from agents.action import create_pending_operation
from agents.policy import PolicyReason, assess_risk
from agents.schemas import (
    ActionProposal,
    ActorContext,
    RiskLevel,
    ToolOutcome,
)
from backend.governance.executor import (
    ERROR_POLICY_DENIED,
    ToolAdapters,
    WriteToolExecutor,
)
from tests.fakes.tools import (
    InMemoryAuditSink,
    RecordingKnowledgeStore,
    RecordingOrderRepository,
    RecordingServiceRepository,
    RecordingUserRepository,
    knowledge_result,
    order_result,
    service_result,
    user_result,
)
from tools.access import request_access
from tools.schemas import (
    CreateTicketArgs,
    CreateTicketResult,
    RequestAccessArgs,
    RequestAccessResult,
    WriteResultStatus,
)
from tools.ticket import create_ticket

AUTH_ACTOR = ActorContext(
    user_id="user-requester-001",
    role="requester",
    authenticated=True,
)

OTHER_ACTOR = ActorContext(
    user_id="user-other-999",
    role="requester",
    authenticated=True,
)


@dataclass
class FakeTicketService:
    calls: list[CreateTicketArgs] = field(default_factory=list)

    async def create_ticket(self, args: CreateTicketArgs) -> CreateTicketResult:
        self.calls.append(args)
        return CreateTicketResult(
            downstream_ticket_id=f"dt-{len(self.calls)}",
            status=WriteResultStatus.CREATED,
        )


@dataclass
class FakeAccessRequestService:
    calls: list[RequestAccessArgs] = field(default_factory=list)

    async def request_access(self, args: RequestAccessArgs) -> RequestAccessResult:
        self.calls.append(args)
        return RequestAccessResult(
            access_request_id=f"ar-{len(self.calls)}",
            status=WriteResultStatus.CREATED,
        )


@dataclass
class UnusedCredentialService:
    async def reset_password(self, args: Any) -> Any:
        raise NotImplementedError


@dataclass
class UnusedRefundService:
    async def process_refund(self, args: Any) -> Any:
        raise NotImplementedError


class FakeLedger:
    def __init__(self) -> None:
        self.ops: dict[str, Any] = {}

    async def get_by_idempotency_key(self, key: str) -> Any:
        d = self.ops.get(key)
        return type("OpRecord", (), d)() if d else None

    async def create_operation(self, **kwargs: Any) -> Any:
        key = kwargs["idempotency_key"]
        self.ops[key] = {**kwargs}
        return type("OpRecord", (), self.ops[key])()

    async def acquire_lease(self, key: str, **kwargs: Any) -> bool:
        rec = self.ops.get(key)
        if not rec or rec.get("status") == "succeeded":
            return False
        rec["status"] = "running"
        return True

    async def record_success(self, key: str, **kwargs: Any) -> Any:
        rec = self.ops[key]
        rec["status"] = "succeeded"
        rec.update(kwargs)
        return type("OpRecord", (), rec)()

    async def record_failure(self, key: str, **kwargs: Any) -> Any:
        rec = self.ops[key]
        rec["status"] = kwargs.get("status", "failed")
        rec.update(kwargs)
        return type("OpRecord", (), rec)()


async def test_domain_create_ticket_calls_service() -> None:
    service = FakeTicketService()
    args = CreateTicketArgs(
        requester_id="user-requester-001",
        title="Network outage follow-up",
        description="Ticket created for further investigation",
        source_ticket_id="src-001",
    )
    result = await create_ticket(args, service)
    assert result.status is WriteResultStatus.CREATED
    assert result.downstream_ticket_id == "dt-1"
    assert len(service.calls) == 1
    assert service.calls[0] == args


async def test_domain_request_access_calls_service() -> None:
    service = FakeAccessRequestService()
    args = RequestAccessArgs(
        requester_id="user-requester-001",
        resource="k8s-prod-cluster",
        justification="Need access to debug production incident",
    )
    result = await request_access(args, service)
    assert result.status is WriteResultStatus.CREATED
    assert result.access_request_id == "ar-1"
    assert len(service.calls) == 1
    assert service.calls[0] == args


async def test_create_ticket_self_request_allowed_by_policy() -> None:
    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="Follow-up ticket",
            description="Details",
            source_ticket_id="src-001",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)
    decision = assess_risk(pending, actor=AUTH_ACTOR)

    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.LOW
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_SELF_SERVICE


async def test_create_ticket_cross_user_denied_by_policy() -> None:
    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="Follow-up ticket",
            description="Details",
            source_ticket_id="src-001",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)
    # OTHER_ACTOR tries to create ticket on behalf of user-requester-001
    decision = assess_risk(pending, actor=OTHER_ACTOR)

    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


async def test_request_access_self_request_allowed_by_policy() -> None:
    proposal = ActionProposal(
        tool_name="request_access",
        arguments=RequestAccessArgs(
            requester_id="user-requester-001",
            resource="vpn-gateway",
            justification="Remote work",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)
    decision = assess_risk(pending, actor=AUTH_ACTOR)

    assert decision.allowed is True
    assert decision.risk_level is RiskLevel.MEDIUM
    assert decision.require_human is False
    assert decision.reason_code is PolicyReason.ALLOW_SELF_SERVICE


async def test_request_access_cross_user_denied_by_policy() -> None:
    proposal = ActionProposal(
        tool_name="request_access",
        arguments=RequestAccessArgs(
            requester_id="user-requester-001",
            resource="vpn-gateway",
            justification="Remote work",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)
    decision = assess_risk(pending, actor=OTHER_ACTOR)

    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_NOT_OWNER


async def test_write_executor_executes_create_ticket_and_idempotent() -> None:
    ticket_service = FakeTicketService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=UnusedCredentialService(),  # type: ignore[arg-type]
        refund=UnusedRefundService(),  # type: ignore[arg-type]
        ticket=ticket_service,
        access_request=FakeAccessRequestService(),
    )
    audit = InMemoryAuditSink()
    ledger = FakeLedger()
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    executor = WriteToolExecutor(
        adapters=adapters,
        audit=audit,
        ticket_id=ticket_id,
        trace_id=trace_id,
        operation_repo=ledger,  # type: ignore[arg-type]
    )

    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="Follow-up ticket",
            description="Details",
            source_ticket_id="src-001",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    # 1. First execution
    res1 = await executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
    )
    assert res1.outcome is ToolOutcome.SUCCEEDED
    assert isinstance(res1.data, CreateTicketResult)
    assert res1.data.status is WriteResultStatus.CREATED
    assert len(ticket_service.calls) == 1

    # 2. Duplicate execution -> CACHE_HIT with zero additional service calls
    res2 = await executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
    )
    assert res2.outcome is ToolOutcome.CACHE_HIT
    assert len(ticket_service.calls) == 1


async def test_write_executor_cross_user_request_access_refused_zero_calls() -> None:
    access_service = FakeAccessRequestService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=UnusedCredentialService(),  # type: ignore[arg-type]
        refund=UnusedRefundService(),  # type: ignore[arg-type]
        ticket=FakeTicketService(),
        access_request=access_service,
    )
    audit = InMemoryAuditSink()
    ledger = FakeLedger()
    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    executor = WriteToolExecutor(
        adapters=adapters,
        audit=audit,
        ticket_id=ticket_id,
        trace_id=trace_id,
        operation_repo=ledger,  # type: ignore[arg-type]
    )

    proposal = ActionProposal(
        tool_name="request_access",
        arguments=RequestAccessArgs(
            requester_id="user-requester-001",
            resource="admin-db",
            justification="Escalate privileges",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    res = await executor.execute(
        agent_id="action",
        operation=pending,
        actor=OTHER_ACTOR,  # Cross-user!
    )
    assert res.outcome is ToolOutcome.REJECTED
    assert res.error_code == ERROR_POLICY_DENIED
    assert len(access_service.calls) == 0
