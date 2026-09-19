"""Integration tests for approval decision routes, optimistic concurrency, and resume outbox."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import JobOutboxRecord, TicketRecord
from backend.db.repositories.approvals import ApprovalRepository
from backend.db.repositories.operations import OperationRepository
from backend.jobs.tickets import resume_job_id
from tests.integration.conftest import (
    APPROVER_ID,
    APPROVER_PASSWORD,
    APPROVER_USERNAME,
    REQUESTER_PASSWORD,
    REQUESTER_USERNAME,
)

APPROVALS_URL = "/api/approvals"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str, password: str) -> str:
    res = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200, res.text
    return str(res.json()["access_token"])


async def _create_ticket_in_db(session: AsyncSession) -> uuid.UUID:
    ticket_id = uuid.uuid4()
    session.add(
        TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id="user-requester-001",
            user_query="Refund request",
            trace_id=uuid.uuid4(),
            status="waiting_approval",
            step_count=3,
        )
    )
    await session.commit()
    return ticket_id


async def test_non_approver_cannot_access_approval_routes(
    api_client: AsyncClient,
) -> None:
    requester_token = await _login(api_client, REQUESTER_USERNAME, REQUESTER_PASSWORD)
    headers = _bearer(requester_token)

    # List approvals
    res = await api_client.get(APPROVALS_URL, headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"

    # Detail
    dummy_id = str(uuid.uuid4())
    res = await api_client.get(f"{APPROVALS_URL}/{dummy_id}", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"

    # Decision
    res = await api_client.post(
        f"{APPROVALS_URL}/{dummy_id}/decision",
        json={"decision": "approve", "version": 1},
        headers=headers,
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"

    # Unauthenticated
    res = await api_client.get(APPROVALS_URL)
    assert res.status_code == 401


async def test_approver_can_list_and_filter_approvals(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    async with session_factory() as session:
        t1 = await _create_ticket_in_db(session)
        t2 = await _create_ticket_in_db(session)
        repo = ApprovalRepository(session)
        a1 = await repo.create_approval(
            ticket_id=t1,
            operation_id=f"op-list-1-{uuid.uuid4().hex[:8]}",
            tool_name="process_refund",
            risk_level="high",
            args_hash="a" * 64,
            args_summary=[{"name": "amount", "value": "250.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        a2 = await repo.create_approval(
            ticket_id=t2,
            operation_id=f"op-list-2-{uuid.uuid4().hex[:8]}",
            tool_name="process_refund",
            risk_level="high",
            args_hash="b" * 64,
            args_summary=[{"name": "amount", "value": "300.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        # decide a2 to approved
        await repo.decide(
            a2.id,
            expected_version=1,
            decision="approve",
            approver_id=APPROVER_ID,
        )
        await session.commit()
        a1_id, a2_id = str(a1.id), str(a2.id)

    # List all
    res = await api_client.get(APPROVALS_URL, headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    ids = [item["approval_id"] for item in data]
    assert a1_id in ids
    assert a2_id in ids

    # Filter pending
    res = await api_client.get(f"{APPROVALS_URL}?status=pending", headers=headers)
    assert res.status_code == 200
    pending_ids = [item["approval_id"] for item in res.json()]
    assert a1_id in pending_ids
    assert a2_id not in pending_ids

    # Filter approved
    res = await api_client.get(f"{APPROVALS_URL}?status=approved", headers=headers)
    assert res.status_code == 200
    approved_ids = [item["approval_id"] for item in res.json()]
    assert a2_id in approved_ids
    assert a1_id not in approved_ids


async def test_approver_can_view_approval_detail(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=f"op-detail-{uuid.uuid4().hex[:8]}",
            tool_name="process_refund",
            risk_level="high",
            args_hash="c" * 64,
            args_summary=[
                {"name": "order_id", "value": "ord-123"},
                {"name": "amount", "value": "250.00"},
            ],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await session.commit()
        approval_id = str(app.id)

    res = await api_client.get(f"{APPROVALS_URL}/{approval_id}", headers=headers)
    assert res.status_code == 200, res.text
    detail = res.json()
    assert detail["approval_id"] == approval_id
    assert detail["ticket_id"] == str(t_id)
    assert detail["tool_name"] == "process_refund"
    assert detail["risk_level"] == "high"
    assert detail["status"] == "pending"
    assert detail["version"] == 1
    assert detail["args_hash"] == "c" * 64
    assert detail["reason_code"] == "HIGH_VALUE_REFUND"
    assert len(detail["args_summary"]) == 2
    assert detail["args_summary"][0] == {"name": "order_id", "value": "ord-123"}
    assert detail["args_summary"][1] == {"name": "amount", "value": "250.00"}

    # Not found
    not_found_res = await api_client.get(f"{APPROVALS_URL}/{uuid.uuid4()}", headers=headers)
    assert not_found_res.status_code == 404
    assert not_found_res.json()["error"]["code"] == "APPROVAL_NOT_FOUND"

    # Invalid ID
    bad_res = await api_client.get(f"{APPROVALS_URL}/not-a-uuid", headers=headers)
    assert bad_res.status_code == 404
    assert bad_res.json()["error"]["code"] == "APPROVAL_NOT_FOUND"


async def test_approver_can_approve_and_creates_resume_outbox(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    op_id = f"op-approve-{uuid.uuid4().hex[:8]}"
    h = "d" * 64
    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        # Seed operation record
        op_repo = OperationRepository(session)
        await op_repo.create_operation(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            args_hash=h,
            idempotency_key=f"idemp-{op_id}",
        )
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            risk_level="high",
            args_hash=h,
            args_summary=[{"name": "amount", "value": "250.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await session.commit()
        approval_id = str(app.id)

    res = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1, "reason": "Manager approved"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["approval_id"] == approval_id
    assert data["status"] == "approved"
    assert data["version"] == 2

    # Check database state
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        rec = await repo.get_by_id(approval_id)
        assert rec is not None
        assert rec.status == "approved"
        assert rec.version == 2
        assert rec.approver_id == APPROVER_ID
        assert rec.reason == "Manager approved"
        assert rec.decided_at is not None

        # Verify resume outbox record
        dedupe = resume_job_id(approval_id, 2)
        outbox_res = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.dedupe_key == dedupe)
        )
        outbox = outbox_res.scalar_one_or_none()
        assert outbox is not None
        assert outbox.job_name == "resume_ticket"
        assert outbox.ticket_id == t_id
        assert outbox.payload == {"ticket_id": str(t_id), "approval_id": approval_id}


async def test_approver_can_reject_without_resume_outbox(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    op_id = f"op-reject-{uuid.uuid4().hex[:8]}"
    h = "e" * 64
    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            risk_level="high",
            args_hash=h,
            args_summary=[{"name": "amount", "value": "500.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await session.commit()
        approval_id = str(app.id)

    res = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "reject", "version": 1, "reason": "Denied due to policy"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["approval_id"] == approval_id
    assert data["status"] == "rejected"
    assert data["version"] == 2

    async with session_factory() as session:
        repo = ApprovalRepository(session)
        rec = await repo.get_by_id(approval_id)
        assert rec is not None
        assert rec.status == "rejected"
        assert rec.version == 2
        assert rec.approver_id == APPROVER_ID

        # No resume outbox record
        dedupe = resume_job_id(approval_id, 2)
        outbox_res = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.dedupe_key == dedupe)
        )
        assert outbox_res.scalar_one_or_none() is None


async def test_concurrent_decisions_race_condition_occ(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    op_id = f"op-race-{uuid.uuid4().hex[:8]}"
    h = "f" * 64
    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            risk_level="high",
            args_hash=h,
            args_summary=[{"name": "amount", "value": "350.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await session.commit()
        approval_id = str(app.id)

    # Fire two concurrent decisions with identical version=1
    task1 = api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1, "reason": "Race A"},
        headers=headers,
    )
    task2 = api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1, "reason": "Race B"},
        headers=headers,
    )

    r1, r2 = await asyncio.gather(task1, task2)
    statuses = {r1.status_code, r2.status_code}
    assert statuses == {200, 409}, f"Expected {200, 409}, got {r1.status_code} and {r2.status_code}"

    # Exactly one resume outbox row created
    async with session_factory() as session:
        dedupe = resume_job_id(approval_id, 2)
        outbox_res = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.dedupe_key == dedupe)
        )
        outbox_rows = list(outbox_res.scalars().all())
        assert len(outbox_rows) == 1


async def test_repeat_decision_on_decided_approval_fails(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    op_id = f"op-repeat-{uuid.uuid4().hex[:8]}"
    h = "1" * 64
    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            risk_level="high",
            args_hash=h,
            args_summary=[{"name": "amount", "value": "200.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await session.commit()
        approval_id = str(app.id)

    # First decision succeeds
    r1 = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1},
        headers=headers,
    )
    assert r1.status_code == 200

    # Repeat with old version=1 -> 409
    r2 = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1},
        headers=headers,
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "APPROVAL_CONFLICT"

    # Repeat with new version=2 -> 409 (terminal status)
    r3 = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "reject", "version": 2},
        headers=headers,
    )
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "APPROVAL_CONFLICT"


async def test_expired_approval_cannot_be_decided(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    op_id = f"op-expired-{uuid.uuid4().hex[:8]}"
    h = "2" * 64
    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            risk_level="high",
            args_hash=h,
            args_summary=[{"name": "amount", "value": "200.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) - timedelta(minutes=10),  # expired
        )
        await session.commit()
        approval_id = str(app.id)

    res = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "APPROVAL_EXPIRED"

    # Check status changed to expired
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        rec = await repo.get_by_id(approval_id)
        assert rec is not None
        assert rec.status == "expired"

        # No outbox row
        dedupe = resume_job_id(approval_id, 2)
        outbox_res = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.dedupe_key == dedupe)
        )
        assert outbox_res.scalar_one_or_none() is None


async def test_cannot_edit_parameters_in_decision_body(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    dummy_id = str(uuid.uuid4())
    # Extra field 'amount' forbidden by schema
    res = await api_client.post(
        f"{APPROVALS_URL}/{dummy_id}/decision",
        json={"decision": "approve", "version": 1, "amount": 100},
        headers=headers,
    )
    assert res.status_code == 422


async def test_args_hash_mismatch_prevents_resume(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approver_token = await _login(api_client, APPROVER_USERNAME, APPROVER_PASSWORD)
    headers = _bearer(approver_token)

    op_id = f"op-tamper-{uuid.uuid4().hex[:8]}"
    h_app = "3" * 64
    h_op = "4" * 64
    async with session_factory() as session:
        t_id = await _create_ticket_in_db(session)
        op_repo = OperationRepository(session)
        await op_repo.create_operation(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            args_hash=h_op,  # Tampered / different hash
            idempotency_key=f"idemp-{op_id}",
        )
        repo = ApprovalRepository(session)
        app = await repo.create_approval(
            ticket_id=t_id,
            operation_id=op_id,
            tool_name="process_refund",
            risk_level="high",
            args_hash=h_app,  # Original hash
            args_summary=[{"name": "amount", "value": "200.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await session.commit()
        approval_id = str(app.id)

    res = await api_client.post(
        f"{APPROVALS_URL}/{approval_id}/decision",
        json={"decision": "approve", "version": 1},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "ARGS_HASH_MISMATCH"

    # Verify no resume outbox row
    async with session_factory() as session:
        dedupe = resume_job_id(approval_id, 2)
        outbox_res = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.dedupe_key == dedupe)
        )
        assert outbox_res.scalar_one_or_none() is None
