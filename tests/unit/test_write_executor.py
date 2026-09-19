"""Unit tests for the unified write execution gateway, idempotency, and audit."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import SecretStr

from agents.action import create_pending_operation
from agents.schemas import (
    ActionProposal,
    ActorContext,
    ApprovalSnapshot,
    ApprovalStatus,
    ResourceContext,
    ToolOutcome,
)
from backend.governance.executor import (
    ERROR_APPROVAL_MISMATCH,
    ERROR_APPROVAL_REQUIRED,
    ERROR_NOT_PERMITTED,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN_TOOL,
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
from tools.registry import TOOL_REGISTRY
from tools.schemas import (
    CreateTicketArgs,
    CreateTicketResult,
    ProcessRefundArgs,
    ProcessRefundResult,
    RequestAccessArgs,
    RequestAccessResult,
    ResetPasswordArgs,
    ResetPasswordResult,
    WriteResultStatus,
)

AUTH_ACTOR = ActorContext(
    user_id="user-requester-001",
    role="requester",
    authenticated=True,
)


@dataclass
class RecordingTicketService:
    calls: list[CreateTicketArgs] = field(default_factory=list)

    async def create_ticket(self, args: CreateTicketArgs) -> CreateTicketResult:
        self.calls.append(args)
        return CreateTicketResult(
            downstream_ticket_id=f"dt-{len(self.calls)}",
            status=WriteResultStatus.CREATED,
        )


@dataclass
class RecordingAccessRequestService:
    calls: list[RequestAccessArgs] = field(default_factory=list)

    async def request_access(self, args: RequestAccessArgs) -> RequestAccessResult:
        self.calls.append(args)
        return RequestAccessResult(
            access_request_id=f"ar-{len(self.calls)}",
            status=WriteResultStatus.CREATED,
        )


@dataclass
class RecordingCredentialService:
    calls: list[ResetPasswordArgs] = field(default_factory=list)

    async def reset_password(self, args: ResetPasswordArgs) -> ResetPasswordResult:
        self.calls.append(args)
        return ResetPasswordResult(
            user_id=args.user_id,
            temporary_password=SecretStr("temp-secret-pass-999"),
            credential_version=args.account_version + 1,
        )


@dataclass
class RecordingRefundService:
    calls: list[ProcessRefundArgs] = field(default_factory=list)
    delay_s: float = 0.0

    async def process_refund(self, args: ProcessRefundArgs) -> ProcessRefundResult:
        self.calls.append(args)
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        return ProcessRefundResult(
            refund_id=f"ref-{len(self.calls)}",
            status=WriteResultStatus.SUCCEEDED,
            amount=args.amount,
            currency="USD",
        )


class FakeOperationLedger:
    def __init__(self) -> None:
        self.operations: dict[str, dict[str, Any]] = {}

    async def get_by_idempotency_key(self, idempotency_key: str) -> Any:
        data = self.operations.get(idempotency_key)
        if data is None:
            return None
        return type("OpRecord", (), data)()

    async def create_operation(self, **kwargs: Any) -> Any:
        key = kwargs["idempotency_key"]
        self.operations[key] = {**kwargs}
        return type("OpRecord", (), self.operations[key])()

    async def acquire_lease(
        self, idempotency_key: str, *, lease_holder: str, duration_seconds: int = 60
    ) -> bool:
        if idempotency_key not in self.operations:
            return False
        rec = self.operations[idempotency_key]
        if rec.get("status") == "succeeded":
            return False
        rec["lease_holder"] = lease_holder
        rec["status"] = "running"
        return True

    async def record_success(
        self,
        idempotency_key: str,
        *,
        encrypted_result: str | None = None,
        result_summary: dict[str, object] | None = None,
    ) -> Any:
        rec = self.operations[idempotency_key]
        rec["status"] = "succeeded"
        rec["encrypted_result"] = encrypted_result
        rec["result_summary"] = result_summary
        return type("OpRecord", (), rec)()

    async def record_failure(
        self,
        idempotency_key: str,
        *,
        status: str,
        result_summary: dict[str, object] | None = None,
    ) -> Any:
        rec = self.operations[idempotency_key]
        rec["status"] = status
        rec["result_summary"] = result_summary
        return type("OpRecord", (), rec)()


class WriteHarness:
    def __init__(self, *, timeout_s: float | None = None) -> None:
        self.audit = InMemoryAuditSink()
        self.ledger = FakeOperationLedger()
        self.knowledge = RecordingKnowledgeStore(result=knowledge_result())
        self.users = RecordingUserRepository(result=user_result())
        self.orders = RecordingOrderRepository(result=order_result())
        self.services = RecordingServiceRepository(result=service_result())
        self.credentials = RecordingCredentialService()
        self.refunds = RecordingRefundService()
        self.tickets = RecordingTicketService()
        self.access_requests = RecordingAccessRequestService()

        registry = dict(TOOL_REGISTRY)
        if timeout_s is not None:
            registry["process_refund"] = registry["process_refund"].model_copy(
                update={"timeout_s": timeout_s}
            )

        self.ticket_id = uuid.uuid4()
        self.trace_id = uuid.uuid4()
        self.adapters = ToolAdapters(
            knowledge=self.knowledge,
            user=self.users,
            order=self.orders,
            service=self.services,
            credential=self.credentials,
            refund=self.refunds,
            ticket=self.tickets,
            access_request=self.access_requests,
        )
        self.executor = WriteToolExecutor(
            adapters=self.adapters,
            audit=self.audit,
            operation_repo=self.ledger,  # type: ignore[arg-type]
            ticket_id=self.ticket_id,
            trace_id=self.trace_id,
            registry=registry,
        )


async def test_whitelist_refused_before_adapter() -> None:
    harness = WriteHarness()
    # Supervisor has no write tools in whitelist
    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="T",
            description="D",
            source_ticket_id="src-1",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    result = await harness.executor.execute(
        agent_id="supervisor",
        operation=pending,
        actor=AUTH_ACTOR,
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_NOT_PERMITTED
    assert len(harness.tickets.calls) == 0
    assert len(harness.audit.attempts) == 1
    assert harness.audit.attempts[0].outcome is ToolOutcome.REJECTED


async def test_unknown_tool_refused_before_adapter() -> None:
    harness = WriteHarness()
    # Create fake pending with unregistered tool
    fake_pending = type(
        "FakePending",
        (),
        {
            "operation_id": "op-fake-1",
            "tool_name": "delete_all_users",
            "validated_args": None,
            "args_hash": "0" * 64,
        },
    )()

    result = await harness.executor.execute(
        agent_id="action",
        operation=fake_pending,
        actor=AUTH_ACTOR,
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_UNKNOWN_TOOL
    assert len(harness.audit.attempts) == 1


async def test_high_risk_refund_without_approval_refused() -> None:
    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-1",
            amount=Decimal("200.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
        approval=None,
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_APPROVAL_REQUIRED
    assert len(harness.refunds.calls) == 0
    assert harness.audit.attempts[0].outcome is ToolOutcome.REJECTED


async def test_high_risk_refund_with_mismatched_approval_refused() -> None:
    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-1",
            amount=Decimal("200.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    mismatched_approval = ApprovalSnapshot(
        approval_id="app-1",
        operation_id="op-different-999",
        args_hash=pending.args_hash,
        status=ApprovalStatus.APPROVED,
        version=2,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
        approval=mismatched_approval,
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_APPROVAL_MISMATCH
    assert len(harness.refunds.calls) == 0
    assert harness.audit.attempts[0].outcome is ToolOutcome.REJECTED


async def test_high_risk_refund_with_valid_approval_succeeds() -> None:
    from datetime import UTC, datetime
    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-1",
            amount=Decimal("200.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    valid_approval = ApprovalSnapshot(
        approval_id="app-1",
        operation_id=pending.operation_id,
        args_hash=pending.args_hash,
        status=ApprovalStatus.APPROVED,
        version=2,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
        approval=valid_approval,
    )

    assert result.outcome is ToolOutcome.SUCCEEDED
    assert len(harness.refunds.calls) == 1
    assert result.operation_id == pending.operation_id
    assert harness.audit.attempts[0].outcome is ToolOutcome.SUCCEEDED


async def test_cache_hit_returns_cached_result_without_second_side_effect() -> None:
    from datetime import UTC, datetime
    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-1",
            amount=Decimal("200.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    approval = ApprovalSnapshot(
        approval_id="app-1",
        operation_id=pending.operation_id,
        args_hash=pending.args_hash,
        status=ApprovalStatus.APPROVED,
        version=2,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    # First call
    res_1 = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
        approval=approval,
    )
    assert res_1.outcome is ToolOutcome.SUCCEEDED
    assert len(harness.refunds.calls) == 1

    # Second call with same pending operation
    res_2 = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
        approval=approval,
    )
    assert res_2.outcome is ToolOutcome.CACHE_HIT
    # CRUCIAL: calls to underlying refund service did not increase!
    assert len(harness.refunds.calls) == 1
    assert harness.audit.attempts[-1].outcome is ToolOutcome.CACHE_HIT


async def test_sensitive_fields_redacted_in_audit() -> None:
    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="reset_password",
        arguments=ResetPasswordArgs(
            user_id="user-requester-001",
            account_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)
    resource = ResourceContext(
        resource_type="user",
        resource_id="user-requester-001",
        owner_id="user-requester-001",
        status="active",
        version=1,
    )

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
        resource=resource,
    )
    assert result.outcome is ToolOutcome.SUCCEEDED

    audit = harness.audit.attempts[0]
    assert audit.result_summary is not None
    assert audit.result_summary.get("temporary_password") == "***"
    # Plaintext password was not leaked to audit
    assert "temp-secret-pass-999" not in str(audit.result_summary)


async def test_timeout_records_failure_and_audits_timeout() -> None:
    harness = WriteHarness(timeout_s=0.01)
    harness.refunds.delay_s = 0.05
    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-1",
            amount=Decimal("50.00"),
            currency="USD",
            order_version=1,
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
    )

    assert result.outcome is ToolOutcome.TIMED_OUT
    assert result.error_code == ERROR_TIMEOUT
    assert harness.audit.attempts[0].outcome is ToolOutcome.TIMED_OUT


async def test_cross_user_denied_by_policy() -> None:
    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="other-user-999",
            title="Help",
            description="Desc",
            source_ticket_id="src-1",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
    )

    from backend.governance.executor import ERROR_POLICY_DENIED
    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_POLICY_DENIED
    assert len(harness.tickets.calls) == 0


async def test_concurrent_execution_refused_when_lease_not_acquired() -> None:
    from backend.governance.executor import ERROR_CONCURRENT_EXECUTION
    from backend.governance.idempotency import derive_idempotency_key

    harness = WriteHarness()
    proposal = ActionProposal(
        tool_name="create_ticket",
        arguments=CreateTicketArgs(
            requester_id="user-requester-001",
            title="Help",
            description="Desc",
            source_ticket_id="src-1",
        ),
        evidence_result_ids=[],
    )
    pending = create_pending_operation(proposal)
    idempotency_key = derive_idempotency_key(
        pending.tool_name, pending.operation_id, pending.args_hash
    )
    await harness.ledger.create_operation(
        ticket_id=harness.ticket_id,
        operation_id=pending.operation_id,
        tool_name=pending.tool_name,
        args_hash=pending.args_hash,
        idempotency_key=idempotency_key,
        status="running",
        lease_holder="other-worker",
    )

    # Force acquire_lease to fail (simulating active competitor lease)
    async def mock_acquire_lease(*args: Any, **kwargs: Any) -> bool:
        return False

    harness.ledger.acquire_lease = mock_acquire_lease  # type: ignore[method-assign]

    result = await harness.executor.execute(
        agent_id="action",
        operation=pending,
        actor=AUTH_ACTOR,
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == ERROR_CONCURRENT_EXECUTION
    assert len(harness.tickets.calls) == 0

