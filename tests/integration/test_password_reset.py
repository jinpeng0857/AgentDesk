"""Integration tests for password reset skill and SqlCredentialService."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import ActorContext
from backend.auth.passwords import verify_password
from backend.db.models import TicketRecord, UserRecord
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.encryption import ResultEncryptor
from backend.governance.executor import ToolAdapters, WriteToolExecutor
from backend.integrations.tool_adapters import (
    DisabledAccessRequestService,
    DisabledRefundService,
    DisabledTicketService,
    SqlCredentialService,
    SqlOrderRepository,
    SqlServiceRepository,
    SqlUserRepository,
)
from skills.password_reset.skill import PasswordResetSkill
from tests.support.fakes.tools import RecordingKnowledgeStore, knowledge_result
from tools.schemas import QueryUserArgs, ResetPasswordArgs

AUTH_ACTOR = ActorContext(
    user_id="user-requester-001",
    role="requester",
    authenticated=True,
)


@pytest.fixture
async def seeded_ticket(
    session_factory: async_sessionmaker[AsyncSession], seeded_users: None
) -> uuid.UUID:
    ticket_id = uuid.uuid4()
    async with session_factory() as session:
        ticket = TicketRecord(
            id=ticket_id,
            thread_id=uuid.uuid4().hex,
            requester_id="user-requester-001",
            user_query="I forgot my password and cannot login",
            trace_id=uuid.uuid4(),
            status="running",
        )
        session.add(ticket)
        await session.commit()
    return ticket_id


async def test_sql_credential_service_resets_password_in_postgresql(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    # 1. Fetch initial user state
    async with session_factory() as session:
        user = await session.get(UserRecord, "user-requester-001")
        assert user is not None
        initial_hash = user.password_hash
        initial_version = user.version

    # 2. Reset password
    async with session_factory() as session:
        service = SqlCredentialService(session)
        result = await service.reset_password(
            ResetPasswordArgs(
                user_id="user-requester-001",
                account_version=initial_version,
            )
        )
        await session.commit()

    assert result.user_id == "user-requester-001"
    assert result.credential_version == initial_version + 1
    temp_pass = result.temporary_password.get_secret_value()
    assert temp_pass.startswith("Tmp-")

    # 3. Verify in PostgreSQL
    async with session_factory() as session:
        updated_user = await session.get(UserRecord, "user-requester-001")
        assert updated_user is not None
        assert updated_user.version == initial_version + 1
        assert updated_user.password_hash != initial_hash
        # Verify that the new password_hash verifies against the temporary password
        assert verify_password(result.temporary_password, updated_user.password_hash)


async def test_sql_credential_service_rejects_inactive_user(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    async with session_factory() as session:
        service = SqlCredentialService(session)
        with pytest.raises(PermissionError, match="inactive"):
            await service.reset_password(
                ResetPasswordArgs(
                    user_id="user-inactive-001",
                    account_version=1,
                )
            )


async def test_password_reset_skill_full_integration_with_postgresql_and_encryption(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_ticket: uuid.UUID,
) -> None:
    trace_id = uuid.uuid4()
    operation_id = f"op-reset-{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=SqlCredentialService(session),
            refund=DisabledRefundService(),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        encryptor = ResultEncryptor()
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
            encryptor=encryptor,
        )

        u = await adapters.user.query_user(QueryUserArgs(user_id="user-requester-001"))
        initial_version = u.version

        skill = PasswordResetSkill()
        result = await skill.run(
            user_id="user-requester-001",
            actor=AUTH_ACTOR,
            user_repo=adapters.user,
            write_gateway=executor,
            operation_id=operation_id,
        )
        await session.commit()

    assert result.success is True
    assert result.temporary_password is not None
    temp_pass = result.temporary_password.get_secret_value()
    assert result.notification is not None
    assert temp_pass in result.notification

    # Verify Database Integrity and Non-Leakage
    async with session_factory() as session:
        # Check operation ledger encrypted_result
        op = await OperationRepository(session).get_by_operation_id(operation_id)
        assert op is not None
        assert op.status == "succeeded"
        assert op.encrypted_result is not None
        # Plaintext password is NEVER stored in database column
        assert temp_pass not in op.encrypted_result
        # Decrypting ciphertext with encryptor recovers the plaintext password
        decrypted_json = encryptor.decrypt(op.encrypted_result)
        assert temp_pass in decrypted_json

        # Check audit record
        audit_records = await AuditRepository(session).list_for_ticket(seeded_ticket)
        assert len(audit_records) == 1
        audit = audit_records[0]
        assert audit.result_summary is not None
        assert audit.result_summary.get("temporary_password") == "***"
        assert temp_pass not in str(audit.result_summary)

    # Replay same operation_id -> Cache hit without new password mutation
    async with session_factory() as session:
        adapters = ToolAdapters(
            knowledge=RecordingKnowledgeStore(result=knowledge_result()),
            user=SqlUserRepository(session),
            order=SqlOrderRepository(session),
            service=SqlServiceRepository(session),
            credential=SqlCredentialService(session),
            refund=DisabledRefundService(),
            ticket=DisabledTicketService(),
            access_request=DisabledAccessRequestService(),
        )
        audit_sink = SqlAuditSink(AuditRepository(session))
        operation_repo = OperationRepository(session)
        executor = WriteToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            operation_repo=operation_repo,
            ticket_id=seeded_ticket,
            trace_id=trace_id,
            encryptor=encryptor,
        )

        skill = PasswordResetSkill()
        replay_result = await skill.run(
            user_id="user-requester-001",
            actor=AUTH_ACTOR,
            user_repo=adapters.user,
            write_gateway=executor,
            operation_id=operation_id,
            account_version=initial_version,
        )
        await session.commit()

    assert replay_result.success is True
    # Replay returned the same cached temporary password
    assert replay_result.temporary_password is not None
    assert replay_result.temporary_password.get_secret_value() == temp_pass
