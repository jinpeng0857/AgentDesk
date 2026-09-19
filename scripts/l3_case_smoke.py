"""AgentDesk L3 Three-Case E2E Smoke Drill.

Demonstrates the three core runtime paths:
- Case A: Read-only knowledge retrieval (Supervisor -> Knowledge -> Resolved, 0 write tools).
- Case B: Low-risk self-service write (Supervisor -> Action -> Policy auto-approved -> Resolved,
  0 approvals, 1 side effect).
- Case C: High-risk write with HITL approval (Supervisor -> Action -> Policy -> interrupt at
  waiting_approval -> 0 side effects -> approve -> resume -> Resolved, 1 approval, 1 side effect).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ruff: noqa: E402

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.schemas import (
    ActionProposal,
    FinalResponse,
    Intent,
    KnowledgeAgentOutput,
    KnowledgeRef,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
)
from backend.auth.passwords import hash_password
from backend.config import AppSettings
from backend.db.models import OrderRecord, RefundRecord, TicketRecord, ToolCallRecord, UserRecord
from backend.db.repositories.approvals import ApprovalRepository
from backend.db.repositories.tickets import TicketRepository
from backend.governance.executor import ReadToolExecutor, ToolAdapters
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.tickets import GraphTicketRunner
from tests.fakes.model_client import FakeModelClient
from tests.fakes.tools import (
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
from tools.schemas import ProcessRefundArgs

REQUESTER_ID = "smoke-requester-01"
APPROVER_ID = "smoke-approver-01"


def log(msg: str) -> None:
    print(f"[l3-smoke] {msg}", flush=True)


def to_psycopg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def seed_smoke_users(session: AsyncSession) -> None:
    for user_id, username, role in [
        (REQUESTER_ID, "smoke_requester", "requester"),
        (APPROVER_ID, "smoke_approver", "approver"),
    ]:
        await session.merge(
            UserRecord(
                id=user_id,
                username=username,
                password_hash=hash_password(SecretStr("smoke-secret-pass")),
                role=role,
                active=True,
                masked_email=f"{username[:3]}***@example.com",
            )
        )
    await session.commit()


async def run_case_a(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    settings: AppSettings,
    prompts: dict[str, Any],
) -> None:
    log("=" * 60)
    log("Starting Case A: Read-Only Knowledge Query (VPN SOP)")
    log("=" * 60)

    ticket_id = uuid.uuid4()
    trace_id = uuid.uuid4()

    async with session_factory() as session:
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query="公司 VPN 无法连接，请查询当前服务状态并给出恢复 SOP。",
                trace_id=trace_id,
                status="queued",
                step_count=0,
            )
        )
        await session.commit()

    fake_knowledge_result = knowledge_result()
    sample_ref = KnowledgeRef(
        document_id=fake_knowledge_result.refs[0].document_id,
        chunk_id=fake_knowledge_result.refs[0].chunk_id,
        title=fake_knowledge_result.refs[0].title,
        content_excerpt=fake_knowledge_result.refs[0].content_excerpt,
        score=fake_knowledge_result.refs[0].score,
    )
    knowledge_store = RecordingKnowledgeStore(result=fake_knowledge_result)
    adapters = ToolAdapters(
        knowledge=knowledge_store,
        user=RecordingUserRepository(result=user_result()),
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=DisabledCredentialService(),
        refund=DisabledRefundService(),
        ticket=DisabledTicketService(),
        access_request=DisabledAccessRequestService(),
    )
    audit_sink = InMemoryAuditSink()
    tool_executor = ReadToolExecutor(
        adapters=adapters,
        audit=audit_sink,
        ticket_id=ticket_id,
        trace_id=trace_id,
    )

    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            KnowledgeAgentOutput(
                answer="VPN is currently undergoing maintenance. Please follow restart steps.",
                knowledge_refs=[sample_ref],
                fallback=False,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="VPN is undergoing maintenance. Please follow restart steps.",
            ),
        ]
    )

    ctx = GraphContext(
        model_client=model,
        prompts=prompts,
        settings=settings,
        tool_executor=tool_executor,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(ctx, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    final_status = await runner.run(str(ticket_id))
    if final_status != TicketStatus.RESOLVED:
        raise AssertionError(f"Case A expected status resolved, got {final_status}")

    async with session_factory() as session:
        repo = TicketRepository(session)
        ticket = await repo.get(ticket_id)
        assert ticket is not None
        assert ticket.status == "resolved"
        assert ticket.final_response is not None

        refs = await repo.list_knowledge_refs(ticket_id)
        assert len(refs) >= 1

        for ref in refs:
            assert ref.document_id and ref.title and (0.0 <= ref.score <= 1.0)

        write_tools = ["process_refund", "reset_password", "create_ticket", "request_access"]
        write_audits = (
            await session.scalars(
                select(ToolCallRecord).where(
                    ToolCallRecord.ticket_id == ticket_id,
                    ToolCallRecord.tool_name.in_(write_tools),
                )
            )
        ).all()
        if len(write_audits) > 0:
            raise AssertionError(f"Case A had unexpected write tool audits: {write_audits}")

    log("[Case A] PASSED: Resolved with complete citations and 0 write tool executions.")


async def run_case_b(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    settings: AppSettings,
    prompts: dict[str, Any],
) -> None:
    log("=" * 60)
    log("Starting Case B: Self-Service Low-Risk Refund ($50 <= $200)")
    log("=" * 60)

    ticket_id = uuid.uuid4()
    order_id = f"ord-b-{uuid.uuid4().hex[:8]}"
    initial_amount = Decimal("100.00")
    refund_amount = Decimal("50.00")

    async with session_factory() as session:
        session.add(
            OrderRecord(
                id=order_id,
                requester_id=REQUESTER_ID,
                refundable_amount=initial_amount,
                currency="USD",
                status="paid",
                version=1,
            )
        )
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query=f"I would like a partial refund of 50 USD for order {order_id}",
                trace_id=uuid.uuid4(),
                status="queued",
                step_count=0,
            )
        )
        await session.commit()

    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.QUERY,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="process_refund",
                arguments=ProcessRefundArgs(
                    requester_id=REQUESTER_ID,
                    order_id=order_id,
                    amount=refund_amount,
                    currency="USD",
                    order_version=1,
                ),
                evidence_result_ids=[],
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response=f"Refund of {refund_amount} USD processed for order {order_id}.",
            ),
        ]
    )

    ctx = GraphContext(
        model_client=model,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(ctx, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    final_status = await runner.run(str(ticket_id))
    if final_status != TicketStatus.RESOLVED:
        raise AssertionError(f"Case B expected status resolved, got {final_status}")

    async with session_factory() as session:
        ticket = await TicketRepository(session).get(ticket_id)
        assert ticket is not None
        assert ticket.status == "resolved"

        apps = await ApprovalRepository(session).list_for_ticket(ticket_id)
        assert len(apps) == 0, f"Case B should require 0 approvals, found {len(apps)}"

        refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert refund_count == 1, f"Expected 1 refund record, got {refund_count}"

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == initial_amount - refund_amount
        assert order.version == 2

    log("[Case B] PASSED: Self-service refund resolved with 0 human approvals and 1 execution.")


async def run_case_c(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
    settings: AppSettings,
    prompts: dict[str, Any],
) -> None:
    log("=" * 60)
    log("Starting Case C: High-Risk Refund ($250 > $200) with HITL Approval & Resume")
    log("=" * 60)

    ticket_id = uuid.uuid4()
    order_id = f"ord-c-{uuid.uuid4().hex[:8]}"
    initial_amount = Decimal("500.00")
    refund_amount = Decimal("250.00")

    async with session_factory() as session:
        session.add(
            OrderRecord(
                id=order_id,
                requester_id=REQUESTER_ID,
                refundable_amount=initial_amount,
                currency="USD",
                status="paid",
                version=1,
            )
        )
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query=f"I want a refund for order {order_id} of 250 USD",
                trace_id=uuid.uuid4(),
                status="queued",
                step_count=0,
            )
        )
        await session.commit()

    model = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.HIGH_RISK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="process_refund",
                arguments=ProcessRefundArgs(
                    requester_id=REQUESTER_ID,
                    order_id=order_id,
                    amount=refund_amount,
                    currency="USD",
                    order_version=1,
                ),
                evidence_result_ids=[],
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.HUMAN_REQUIRED,
                final_response=f"Approved refund of {refund_amount} USD for order {order_id}.",
            ),
        ]
    )

    ctx = GraphContext(
        model_client=model,
        prompts=prompts,
        settings=settings,
        session_factory=session_factory,
    )
    graph = build_ticket_graph(ctx, checkpointer=checkpointer)
    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        graph=graph,
    )

    log("Step 1: Running graph until HITL interrupt...")
    interrupted_status = await runner.run(str(ticket_id))
    assert interrupted_status == TicketStatus.WAITING_APPROVAL, (
        f"Expected waiting_approval, got {interrupted_status}"
    )

    async with session_factory() as session:
        t_rec = await TicketRepository(session).get(ticket_id)
        assert t_rec is not None
        assert t_rec.status == "waiting_approval"

        app_repo = ApprovalRepository(session)
        apps = await app_repo.list_for_ticket(ticket_id)
        assert len(apps) == 1
        approval = apps[0]
        assert approval.status == "pending"
        assert approval.tool_name == "process_refund"
        assert approval.risk_level == "high"

        pre_refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert pre_refund_count == 0, f"Expected 0 refund records, got {pre_refund_count}"

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == initial_amount
        assert order.version == 1

    log("Step 1 verified: Graph suspended at waiting_approval, 1 pending approval, 0 side-effects.")

    log("Step 2: Approver decides 'approve' via ApprovalRepository...")
    async with session_factory() as session:
        app_repo = ApprovalRepository(session)
        decided_record = await app_repo.decide(
            approval_id=approval.id,
            expected_version=1,
            decision="approve",
            approver_id=APPROVER_ID,
            reason="Approved after customer eligibility review",
        )
        assert decided_record.status == "approved"
        assert decided_record.version == 2
        await session.commit()

    log("Step 2 verified: Approval transitioned to approved (v2).")

    log("Step 3: Resuming graph execution with approved ID...")
    resume_status = await runner.resume(str(ticket_id), str(approval.id))
    assert resume_status == TicketStatus.RESOLVED, (
        f"Expected resolved after resume, got {resume_status}"
    )

    async with session_factory() as session:
        t_rec = await TicketRepository(session).get(ticket_id)
        assert t_rec is not None
        assert t_rec.status == "resolved"

        post_refund_count = await session.scalar(
            select(func.count())
            .select_from(RefundRecord)
            .where(RefundRecord.ticket_id == ticket_id)
        )
        assert post_refund_count == 1, f"Expected 1 refund record, got {post_refund_count}"

        order = await session.get(OrderRecord, order_id)
        assert order is not None
        assert order.refundable_amount == initial_amount - refund_amount
        assert order.version == 2

    log("[Case C] PASSED: Suspended -> Zero Side Effects -> Approved -> Resumed -> 1 execution.")


async def main_async(db_url: str) -> None:
    log(f"Initializing database connection: {db_url}...")
    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    psycopg_url = to_psycopg_url(db_url)
    pool = AsyncConnectionPool(
        conninfo=psycopg_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    async with session_factory() as session:
        await seed_smoke_users(session)

    prompts = load_all_prompts()
    settings = AppSettings(
        database_url=db_url,
        jwt_secret="smoke-secret-at-least-32-bytes-long-for-testing",
        openai_model="test-model",
    )

    try:
        await run_case_a(session_factory, checkpointer, settings, prompts)
        await run_case_b(session_factory, checkpointer, settings, prompts)
        await run_case_c(session_factory, checkpointer, settings, prompts)

        log("=" * 60)
        log("ALL THREE L3 DEMO CASES (A, B, C) PASSED END-TO-END!")
        log("=" * 60)
    finally:
        await pool.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="L3 Three-Case E2E Smoke Drill")
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:5432/agentdesk_test",
        ),
        help="PostgreSQL database URL",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main_async(args.database_url))
        return 0
    except Exception as exc:
        log(f"SMOKE FAILED: {exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
