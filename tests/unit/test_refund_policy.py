"""Unit tests for refund policy, boundaries, and write gateway gating."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import ValidationError

from agents.action import create_pending_operation
from agents.policy import (
    REFUND_APPROVAL_THRESHOLD_USD,
    assess_risk,
)
from agents.schemas import (
    ActionProposal,
    ActorContext,
    ApprovalSnapshot,
    ApprovalStatus,
    PendingOperation,
    PolicyReason,
    ResourceContext,
    RiskLevel,
    ToolOutcome,
)
from backend.governance.executor import (
    ERROR_APPROVAL_MISMATCH,
    ERROR_APPROVAL_REQUIRED,
    ToolAdapters,
    WriteToolExecutor,
)
from tests.support.fakes.tools import (
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
from tests.unit.test_write_executor import (
    RecordingAccessRequestService,
    RecordingCredentialService,
    RecordingRefundService,
    RecordingTicketService,
)
from tests.unit.test_write_tools import FakeLedger
from tools.schemas import (
    ProcessRefundArgs,
    ProcessRefundResult,
    WriteResultStatus,
)
from tools.ticket import process_refund

AUTH_ACTOR = ActorContext(
    user_id="user-requester-001",
    role="requester",
    authenticated=True,
)


def _make_refund_op(
    amount: Decimal,
    order_id: str = "ord-001",
    requester_id: str = "user-requester-001",
    order_version: int = 1,
    operation_id: str | None = None,
) -> PendingOperation:
    proposal = ActionProposal(
        tool_name="process_refund",
        arguments=ProcessRefundArgs(
            requester_id=requester_id,
            order_id=order_id,
            amount=amount,
            currency="USD",
            order_version=order_version,
        ),
        evidence_result_ids=[],
    )
    return create_pending_operation(proposal, operation_id=operation_id)


def test_refund_amount_boundary_199_99_is_medium_risk() -> None:
    amount = Decimal("199.99")
    assert amount < REFUND_APPROVAL_THRESHOLD_USD
    op = _make_refund_op(amount)
    res = ResourceContext(
        resource_type="order",
        resource_id="ord-001",
        owner_id="user-requester-001",
        status="paid",
        version=1,
    )
    decision = assess_risk(op, actor=AUTH_ACTOR, resource=res)

    assert decision.allowed is True
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.require_human is False
    assert decision.reason_code == PolicyReason.ALLOW_LOW_REFUND


def test_refund_amount_boundary_200_00_is_high_risk_and_requires_human() -> None:
    amount = Decimal("200.00")
    assert amount >= REFUND_APPROVAL_THRESHOLD_USD
    op = _make_refund_op(amount)
    res = ResourceContext(
        resource_type="order",
        resource_id="ord-001",
        owner_id="user-requester-001",
        status="paid",
        version=1,
    )
    decision = assess_risk(op, actor=AUTH_ACTOR, resource=res)

    assert decision.allowed is True
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.require_human is True
    assert decision.reason_code == PolicyReason.REQUIRES_REFUND_APPROVAL


def test_refund_large_amount_is_high_risk_and_requires_human() -> None:
    amount = Decimal("500.00")
    op = _make_refund_op(amount)
    decision = assess_risk(op, actor=AUTH_ACTOR)

    assert decision.allowed is True
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.require_human is True
    assert decision.reason_code == PolicyReason.REQUIRES_REFUND_APPROVAL


def test_refund_denies_requester_mismatch() -> None:
    op = _make_refund_op(
        amount=Decimal("50.00"),
        requester_id="user-other-999",
    )
    decision = assess_risk(op, actor=AUTH_ACTOR)

    assert decision.allowed is False
    assert decision.reason_code == PolicyReason.DENY_NOT_OWNER


def test_refund_denies_resource_owner_mismatch() -> None:
    op = _make_refund_op(amount=Decimal("50.00"))
    res = ResourceContext(
        resource_type="order",
        resource_id="ord-001",
        owner_id="user-other-999",
        status="paid",
        version=1,
    )
    decision = assess_risk(op, actor=AUTH_ACTOR, resource=res)

    assert decision.allowed is False
    assert decision.reason_code == PolicyReason.DENY_NOT_OWNER


def test_refund_schema_validates_decimal_and_rejects_invalid_values() -> None:
    # 1. Valid pure Decimal
    args = ProcessRefundArgs(
        requester_id="user-requester-001",
        order_id="ord-001",
        amount=Decimal("199.99"),
        currency="USD",
        order_version=1,
    )
    assert isinstance(args.amount, Decimal)
    assert args.amount == Decimal("199.99")

    # 2. Negative amount rejected by schema
    with pytest.raises(ValidationError):
        ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-001",
            amount=Decimal("-10.00"),
            currency="USD",
            order_version=1,
        )

    # 3. Currency other than USD rejected by schema
    with pytest.raises(ValidationError):
        ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-001",
            amount=Decimal("50.00"),
            currency=cast(Any, "EUR"),
            order_version=1,
        )

    # 4. Non-positive order_version rejected
    with pytest.raises(ValidationError):
        ProcessRefundArgs(
            requester_id="user-requester-001",
            order_id="ord-001",
            amount=Decimal("50.00"),
            currency="USD",
            order_version=0,
        )


async def test_domain_process_refund_delegates_to_service() -> None:
    service = RecordingRefundService()
    args = ProcessRefundArgs(
        requester_id="user-requester-001",
        order_id="ord-001",
        amount=Decimal("199.99"),
        currency="USD",
        order_version=1,
    )
    result = await process_refund(args, service)
    assert result.status == WriteResultStatus.SUCCEEDED
    assert result.amount == Decimal("199.99")
    assert len(service.calls) == 1


async def test_gateway_auto_executes_medium_refund_without_approval() -> None:
    refund_service = RecordingRefundService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=RecordingCredentialService(),
        refund=refund_service,
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )
    executor = WriteToolExecutor(
        adapters=adapters,
        audit=InMemoryAuditSink(),
        operation_repo=cast(Any, FakeLedger()),
        ticket_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )

    op = _make_refund_op(Decimal("199.99"))
    result = await executor.execute(
        agent_id="action",
        operation=op,
        actor=AUTH_ACTOR,
    )

    assert result.outcome == ToolOutcome.SUCCEEDED
    assert isinstance(result.data, ProcessRefundResult)
    assert result.data.amount == Decimal("199.99")
    assert len(refund_service.calls) == 1


async def test_gateway_blocks_high_refund_without_approval() -> None:
    refund_service = RecordingRefundService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=RecordingCredentialService(),
        refund=refund_service,
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )
    executor = WriteToolExecutor(
        adapters=adapters,
        audit=InMemoryAuditSink(),
        operation_repo=cast(Any, FakeLedger()),
        ticket_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )

    op = _make_refund_op(Decimal("200.00"))
    result = await executor.execute(
        agent_id="action",
        operation=op,
        actor=AUTH_ACTOR,
    )

    assert result.outcome == ToolOutcome.REJECTED
    assert result.error_code == ERROR_APPROVAL_REQUIRED
    # Side-effect count is strictly 0
    assert len(refund_service.calls) == 0


async def test_gateway_executes_high_refund_with_valid_approval() -> None:
    refund_service = RecordingRefundService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=RecordingCredentialService(),
        refund=refund_service,
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )
    executor = WriteToolExecutor(
        adapters=adapters,
        audit=InMemoryAuditSink(),
        operation_repo=cast(Any, FakeLedger()),
        ticket_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )

    op = _make_refund_op(Decimal("200.00"))
    approval = ApprovalSnapshot(
        approval_id="app-refund-001",
        operation_id=op.operation_id,
        args_hash=op.args_hash,
        status=ApprovalStatus.APPROVED,
        version=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    result = await executor.execute(
        agent_id="action",
        operation=op,
        actor=AUTH_ACTOR,
        approval=approval,
    )

    assert result.outcome == ToolOutcome.SUCCEEDED
    assert isinstance(result.data, ProcessRefundResult)
    assert result.data.amount == Decimal("200.00")
    assert len(refund_service.calls) == 1


async def test_gateway_rejects_high_refund_when_args_hash_or_version_mismatch() -> None:
    refund_service = RecordingRefundService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=RecordingCredentialService(),
        refund=refund_service,
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )
    executor = WriteToolExecutor(
        adapters=adapters,
        audit=InMemoryAuditSink(),
        operation_repo=cast(Any, FakeLedger()),
        ticket_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )

    # Initial operation with version 1
    op_v1 = _make_refund_op(Decimal("200.00"), order_version=1)
    # Approved for version 1
    approval_v1 = ApprovalSnapshot(
        approval_id="app-refund-001",
        operation_id=op_v1.operation_id,
        args_hash=op_v1.args_hash,
        status=ApprovalStatus.APPROVED,
        version=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    # Now order version changed to 2!
    op_v2 = _make_refund_op(
        Decimal("200.00"),
        order_version=2,
        operation_id=op_v1.operation_id,
    )
    assert op_v2.args_hash != op_v1.args_hash

    # Reusing approval_v1 on mutated operation must fail closed
    result = await executor.execute(
        agent_id="action",
        operation=op_v2,
        actor=AUTH_ACTOR,
        approval=approval_v1,
    )

    assert result.outcome == ToolOutcome.REJECTED
    assert result.error_code == ERROR_APPROVAL_MISMATCH
    assert len(refund_service.calls) == 0
