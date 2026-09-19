"""Empty-database migration coverage for business and checkpoint schema."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from backend.db.models import TicketRecord
from backend.db.repositories.approvals import ApprovalConflictError, ApprovalRepository
from backend.db.repositories.operations import OperationRepository

EXPECTED_TABLES = {
    "users",
    "orders",
    "service_statuses",
    "tickets",
    "ticket_knowledge_refs",
    "trace_events",
    "tool_call_records",
    "job_outbox",
    "tool_operations",
    "approvals",
    "refunds",
    "access_requests",
    "downstream_tickets",
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


async def table_names(connection: AsyncConnection) -> set[str]:
    result = await connection.scalar(
        text("select array_agg(tablename) from pg_tables where schemaname = 'public'")
    )
    return set(result or [])


async def test_upgrade_head_builds_business_and_checkpoint_schema(
    migrated_connection: AsyncConnection,
) -> None:
    tables = await table_names(migrated_connection)
    assert tables >= EXPECTED_TABLES


async def test_checkpoint_migration_versions_match_installed_library(
    migrated_connection: AsyncConnection,
) -> None:
    version = await migrated_connection.scalar(text("select max(v) from checkpoint_migrations"))
    assert version == len(BasePostgresSaver.MIGRATIONS) - 1


async def test_checkpoint_indexes_and_task_path_column_exist(
    migrated_connection: AsyncConnection,
) -> None:
    indexes = await migrated_connection.scalar(
        text("select array_agg(indexname) from pg_indexes where schemaname = 'public'")
    )
    assert {
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    } <= set(indexes or [])

    columns = await migrated_connection.scalar(
        text(
            "select array_agg(column_name) from information_schema.columns "
            "where table_name = 'checkpoint_writes'"
        )
    )
    assert "task_path" in set(columns or [])


async def test_phase2_governance_and_ledger_schema_exist(
    migrated_connection: AsyncConnection,
) -> None:
    indexes = await migrated_connection.scalar(
        text("select array_agg(indexname) from pg_indexes where schemaname = 'public'")
    )
    assert {
        "ix_tool_operations_idempotency_key",
        "ix_tool_operations_operation_id",
        "ix_approvals_operation_id",
        "ix_refunds_order_id",
        "ix_access_requests_requester_id",
        "ix_downstream_tickets_source_ticket_id",
    } <= set(indexes or [])

    user_cols = await migrated_connection.scalar(
        text(
            "select array_agg(column_name) from information_schema.columns "
            "where table_name = 'users'"
        )
    )
    assert "version" in set(user_cols or [])


async def test_operation_repository_lifecycle(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    async with session_factory() as session:
        ticket = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id="user-requester-001",
            user_query="test write op",
            trace_id=uuid.uuid4(),
            status="running",
        )
        session.add(ticket)
        await session.commit()

    async with session_factory() as session:
        repo = OperationRepository(session)
        op_rec = await repo.create_operation(
            ticket_id=ticket_id,
            operation_id="op-test-001",
            tool_name="process_refund",
            args_hash="a" * 64,
            idempotency_key="key-test-001",
            status="pending",
        )
        await session.commit()
        assert op_rec.idempotency_key == "key-test-001"

    async with session_factory() as session:
        repo = OperationRepository(session)
        acquired = await repo.acquire_lease(
            "key-test-001", lease_holder="worker-1", duration_seconds=60
        )
        assert acquired is True
        await session.commit()

        # Second worker cannot acquire while lease is active
        acquired_again = await repo.acquire_lease(
            "key-test-001", lease_holder="worker-2", duration_seconds=60
        )
        assert acquired_again is False

        # Record success
        success_rec = await repo.record_success(
            "key-test-001",
            encrypted_result="enc:secret-token",
            result_summary={"status": "succeeded"},
        )
        await session.commit()
        assert success_rec.status == "succeeded"
        assert success_rec.encrypted_result == "enc:secret-token"


async def test_approval_repository_optimistic_locking_and_expiry(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    ticket_id = uuid.uuid4()
    async with session_factory() as session:
        ticket = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id="user-requester-001",
            user_query="test hitl",
            trace_id=uuid.uuid4(),
            status="waiting_approval",
        )
        session.add(ticket)
        await session.commit()

    async with session_factory() as session:
        repo = ApprovalRepository(session)
        approval = await repo.create_approval(
            ticket_id=ticket_id,
            operation_id="op-test-hitl-001",
            tool_name="process_refund",
            risk_level="high",
            args_hash="h" * 64,
            args_summary=[{"name": "amount", "value": "250.00"}],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        approval_id = approval.id
        await session.commit()

    # Mismatched version fails
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        with pytest.raises(ApprovalConflictError):
            await repo.decide(
                approval_id,
                expected_version=99,
                decision="approve",
                approver_id="user-approver-001",
            )

    # Correct version succeeds and bumps version to 2
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        decided = await repo.decide(
            approval_id,
            expected_version=1,
            decision="approve",
            approver_id="user-approver-001",
            reason="approved by manager",
        )
        await session.commit()
        assert decided.status == "approved"
        assert decided.version == 2
        assert decided.decided_at is not None

    # Repeated decision fails (not pending anymore)
    async with session_factory() as session:
        repo = ApprovalRepository(session)
        with pytest.raises(ApprovalConflictError):
            await repo.decide(
                approval_id,
                expected_version=2,
                decision="approve",
                approver_id="user-approver-001",
            )

    # Test expiry
    expired_ticket_id = uuid.uuid4()
    async with session_factory() as session:
        ticket = TicketRecord(
            id=expired_ticket_id,
            thread_id=str(expired_ticket_id),
            requester_id="user-requester-001",
            user_query="test expired",
            trace_id=uuid.uuid4(),
            status="waiting_approval",
        )
        session.add(ticket)
        await session.commit()

    async with session_factory() as session:
        repo = ApprovalRepository(session)
        expired_app = await repo.create_approval(
            ticket_id=expired_ticket_id,
            operation_id="op-test-expired-001",
            tool_name="process_refund",
            risk_level="high",
            args_hash="e" * 64,
            args_summary=[],
            reason_code="HIGH_VALUE_REFUND",
            expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        await session.commit()

        expired_list = await repo.expire_pending()
        await session.commit()
        assert any(r.id == expired_app.id for r in expired_list)


