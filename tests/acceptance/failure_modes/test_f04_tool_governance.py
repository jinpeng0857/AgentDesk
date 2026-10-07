"""Comprehensive failure modes verification (F-01 through F-08).

Day 17: F-01a (read tool continuous timeout -> fallback)
and F-01b (write tool continuous timeout -> escalated).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from agents.idempotency import compute_args_hash
from agents.schemas import (
    ActorContext,
    PendingOperation,
    ToolOutcome,
)
from backend.governance.executor import (
    ERROR_NOT_PERMITTED,
    ERROR_UNKNOWN_TOOL,
    ReadToolExecutor,
    ToolAdapters,
    WriteToolExecutor,
)
from tests.integration.conftest import REQUESTER_ID
from tests.support.fakes.tools import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledRefundService,
    DisabledTicketService,
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
from tools.registry import (
    AGENT_TOOL_WHITELIST,
    TOOL_MODEL_PAIRS,
    TOOL_REGISTRY,
)
from tools.schemas import (
    ProcessRefundArgs,
)


async def test_f04_unauthorized_and_unknown_tool_rejections_have_zero_side_effects() -> None:

    """F-04: Gateway rejects unknown or unauthorized tools with audit and 0 side effects.



    1. delete_user is NOT in TOOL_REGISTRY, not in TOOL_MODEL_PAIRS, and has no callable.

    2. Supervisor calling read tool (query_user) or write tool (process_refund) is rejected.

    3. Knowledge calling write tool (process_refund) is rejected.

    4. Action agent calling unknown tool (delete_user) is rejected before adapter lookup.

    5. All rejected attempts generate audit records with ToolOutcome.REJECTED and 0 side effects.

    """

    # 1. Registry verification: delete_user must not exist in production registries

    assert "delete_user" not in TOOL_REGISTRY

    assert "delete_user" not in TOOL_MODEL_PAIRS

    assert "delete_user" not in AGENT_TOOL_WHITELIST.get("action", ())

    assert "delete_user" not in AGENT_TOOL_WHITELIST.get("supervisor", ())

    assert "delete_user" not in AGENT_TOOL_WHITELIST.get("knowledge", ())



    # 2. Setup mock executors with tracking

    audit_sink = InMemoryAuditSink()

    user_repo = RecordingUserRepository(result=user_result())

    order_repo = RecordingOrderRepository(result=order_result())

    knowledge_store = RecordingKnowledgeStore(result=knowledge_result())

    service_repo = RecordingServiceRepository(result=service_result())

    refund_service = DisabledRefundService()

    cred_service = DisabledCredentialService()

    ticket_service = DisabledTicketService()

    access_service = DisabledAccessRequestService()



    adapters = ToolAdapters(

        knowledge=knowledge_store,

        user=user_repo,

        order=order_repo,

        service=service_repo,

        credential=cred_service,

        refund=refund_service,

        ticket=ticket_service,

        access_request=access_service,

    )



    ticket_id = uuid.uuid4()

    trace_id = uuid.uuid4()

    read_executor = ReadToolExecutor(

        adapters=adapters,

        audit=audit_sink,

        ticket_id=ticket_id,

        trace_id=trace_id,

    )

    write_executor = WriteToolExecutor(

        adapters=adapters,

        audit=audit_sink,

        ticket_id=ticket_id,

        trace_id=trace_id,

        operation_repo=None,

    )

    actor = ActorContext(user_id=REQUESTER_ID, role="requester", authenticated=True)



    refund_args = ProcessRefundArgs(

        requester_id=REQUESTER_ID,

        order_id="ord-f04-1",

        amount=Decimal("10.00"),

        currency="USD",

        order_version=1,

    )

    op_refund = PendingOperation(

        operation_id=uuid.uuid4().hex,

        tool_name="process_refund",

        validated_args=refund_args,

        args_hash=compute_args_hash(refund_args),

        side_effects="write",

        evidence_result_ids=[],

        proposed_by="action",

        proposed_at=datetime.now(UTC),

        schema_version="1.0.0",

    )



    # 3. Vector A: Supervisor calling tools

    # A1: Supervisor calls read tool (query_user) -> rejected

    res_sup_read = await read_executor.execute(

        agent_id="supervisor",

        tool_name="query_user",

        raw_args={"user_id": REQUESTER_ID},

    )

    assert res_sup_read.outcome == ToolOutcome.REJECTED

    assert res_sup_read.error_code == ERROR_NOT_PERMITTED

    assert len(user_repo.calls) == 0



    # A2: Supervisor calls write tool (process_refund) via WriteToolExecutor -> rejected

    res_sup_write = await write_executor.execute(

        agent_id="supervisor",

        operation=op_refund,

        actor=actor,

    )

    assert res_sup_write.outcome == ToolOutcome.REJECTED

    assert res_sup_write.error_code == ERROR_NOT_PERMITTED

    assert len(refund_service.calls) == 0



    # 4. Vector B: Knowledge calling write tool

    # B1: Knowledge calls write tool via WriteToolExecutor -> rejected

    res_kno_write = await write_executor.execute(

        agent_id="knowledge",

        operation=op_refund,

        actor=actor,

    )

    assert res_kno_write.outcome == ToolOutcome.REJECTED

    assert res_kno_write.error_code == ERROR_NOT_PERMITTED

    assert len(refund_service.calls) == 0



    # B2: Knowledge calls write tool via ReadToolExecutor -> rejected

    res_kno_read = await read_executor.execute(

        agent_id="knowledge",

        tool_name="process_refund",

        raw_args={"order_id": "ord-f04-1", "amount": "10.00", "currency": "USD"},

    )

    assert res_kno_read.outcome == ToolOutcome.REJECTED

    assert res_kno_read.error_code == ERROR_NOT_PERMITTED

    assert len(refund_service.calls) == 0



    # 5. Vector C: Unknown tool delete_user

    # C1: delete_user via ReadToolExecutor -> rejected before adapter lookup

    res_del_read = await read_executor.execute(

        agent_id="action",

        tool_name="delete_user",

        raw_args={"user_id": REQUESTER_ID},

    )

    assert res_del_read.outcome == ToolOutcome.REJECTED

    assert res_del_read.error_code == ERROR_UNKNOWN_TOOL



    # C2: delete_user via WriteToolExecutor -> rejected before adapter lookup

    res_del_write = await write_executor.execute(

        agent_id="action",

        operation={

            "tool_name": "delete_user",

            "operation_id": "op-del",

            "arguments": {"user_id": REQUESTER_ID},

        },

        actor=actor,

    )

    assert res_del_write.outcome == ToolOutcome.REJECTED

    assert res_del_write.error_code == ERROR_UNKNOWN_TOOL



    # 6. Audit and side effect assertions

    assert len(audit_sink.attempts) == 6

    for attempt in audit_sink.attempts:

        assert attempt.outcome == ToolOutcome.REJECTED



    # Total side effects across domain services: strictly zero

    assert len(refund_service.calls) == 0

    assert len(user_repo.calls) == 0

    assert len(cred_service.calls) == 0

    assert len(ticket_service.calls) == 0

    assert len(access_service.calls) == 0
