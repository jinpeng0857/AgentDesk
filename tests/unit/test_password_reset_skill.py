"""Unit tests for the deterministic password reset skill and result encryption."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import SecretStr

from agents.schemas import ActorContext
from backend.governance.encryption import ResultEncryptor
from backend.governance.executor import ToolAdapters, WriteToolExecutor
from skills.password_reset.skill import PasswordResetSkill
from skills.password_reset.validate_user import UserValidationError, validate_user_for_reset
from tests.support.fakes.tools import (
    InMemoryAuditSink,
    RecordingKnowledgeStore,
    RecordingOrderRepository,
    RecordingServiceRepository,
    knowledge_result,
    order_result,
    service_result,
)
from tests.unit.test_write_executor import (
    RecordingAccessRequestService,
    RecordingRefundService,
    RecordingTicketService,
)
from tools.schemas import (
    AccountStatus,
    QueryUserArgs,
    QueryUserResult,
    ResetPasswordArgs,
    ResetPasswordResult,
)

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


class FakeUserRepository:
    def __init__(self, user: QueryUserResult | None = None) -> None:
        self.user = user

    async def query_user(self, args: QueryUserArgs) -> QueryUserResult:
        if self.user is None or self.user.user_id != args.user_id:
            raise LookupError(f"user {args.user_id} not found")
        return self.user


class RecordingCredentialService:
    def __init__(self) -> None:
        self.calls: list[ResetPasswordArgs] = []

    async def reset_password(self, args: ResetPasswordArgs) -> ResetPasswordResult:
        self.calls.append(args)
        return ResetPasswordResult(
            user_id=args.user_id,
            temporary_password=SecretStr(f"TempPass-{len(self.calls)}"),
            credential_version=args.account_version + 1,
        )


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


def test_result_encryptor_round_trip() -> None:
    encryptor = ResultEncryptor()
    plaintext = '{"user_id": "user-001", "temporary_password": "super-secret-password-123"}'

    token = encryptor.encrypt(plaintext)
    assert token != plaintext
    # Ensure plaintext password does not appear in ciphertext
    assert "super-secret-password-123" not in token

    decrypted = encryptor.decrypt(token)
    assert decrypted == plaintext


def test_result_encryptor_rejects_corrupted_token() -> None:
    encryptor = ResultEncryptor()
    with pytest.raises((ValueError, InvalidTag)):
        encryptor.decrypt("invalid-token")


def test_validate_user_for_reset_preconditions() -> None:
    active_user = QueryUserResult(
        user_id="user-requester-001",
        account_status=AccountStatus.ACTIVE,
        masked_email="r***@example.com",
        version=1,
    )

    # 1. Valid owner & active
    validate_user_for_reset(active_user, AUTH_ACTOR)

    # 2. Not owner raises UserValidationError
    with pytest.raises(UserValidationError, match="not owner"):
        validate_user_for_reset(active_user, OTHER_ACTOR)

    # 3. Inactive account raises UserValidationError
    inactive_user = QueryUserResult(
        user_id="user-requester-001",
        account_status=AccountStatus.INACTIVE,
        masked_email="r***@example.com",
        version=1,
    )
    with pytest.raises(UserValidationError, match="inactive"):
        validate_user_for_reset(inactive_user, AUTH_ACTOR)


async def test_password_reset_skill_full_happy_path() -> None:
    user = QueryUserResult(
        user_id="user-requester-001",
        account_status=AccountStatus.ACTIVE,
        masked_email="r***@example.com",
        version=1,
    )
    user_repo = FakeUserRepository(user)
    cred_service = RecordingCredentialService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=user_repo,
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=cred_service,
        refund=RecordingRefundService(),
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )
    executor = WriteToolExecutor(
        adapters=adapters,
        audit=InMemoryAuditSink(),
        operation_repo=FakeLedger(),  # type: ignore[arg-type]
        ticket_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )

    skill = PasswordResetSkill()
    result = await skill.run(
        user_id="user-requester-001",
        actor=AUTH_ACTOR,
        user_repo=user_repo,
        write_gateway=executor,
    )

    assert result.success is True
    assert result.user_id == "user-requester-001"
    assert result.temporary_password is not None
    assert result.temporary_password.get_secret_value() == "TempPass-1"
    assert result.notification is not None
    assert "TempPass-1" in result.notification
    assert result.step_history == [
        "query_user",
        "validate_user",
        "formulate_operation",
        "execute_gateway",
        "render_notification",
    ]


async def test_password_reset_skill_fails_when_user_not_found() -> None:
    user_repo = FakeUserRepository(None)
    skill = PasswordResetSkill()
    dummy_executor = type("Dummy", (), {})()

    result = await skill.run(
        user_id="user-non-existent",
        actor=AUTH_ACTOR,
        user_repo=user_repo,
        write_gateway=dummy_executor,
    )

    assert result.success is False
    assert result.error_code == "USER_LOOKUP_FAILED"
    assert result.step_history == ["query_user"]


async def test_password_reset_skill_fails_on_cross_user() -> None:
    user = QueryUserResult(
        user_id="user-requester-001",
        account_status=AccountStatus.ACTIVE,
        masked_email="r***@example.com",
        version=1,
    )
    user_repo = FakeUserRepository(user)
    skill = PasswordResetSkill()
    dummy_executor = type("Dummy", (), {})()

    result = await skill.run(
        user_id="user-requester-001",
        actor=OTHER_ACTOR,  # Cross-user!
        user_repo=user_repo,
        write_gateway=dummy_executor,
    )

    assert result.success is False
    assert result.error_code == "VALIDATION_FAILED"
    assert result.step_history == ["query_user", "validate_user"]


async def test_password_reset_skill_idempotent_replay() -> None:
    user = QueryUserResult(
        user_id="user-requester-001",
        account_status=AccountStatus.ACTIVE,
        masked_email="r***@example.com",
        version=1,
    )
    user_repo = FakeUserRepository(user)
    cred_service = RecordingCredentialService()
    adapters = ToolAdapters(
        knowledge=RecordingKnowledgeStore(result=knowledge_result()),
        user=user_repo,
        order=RecordingOrderRepository(result=order_result()),
        service=RecordingServiceRepository(result=service_result()),
        credential=cred_service,
        refund=RecordingRefundService(),
        ticket=RecordingTicketService(),
        access_request=RecordingAccessRequestService(),
    )
    ledger = FakeLedger()
    executor = WriteToolExecutor(
        adapters=adapters,
        audit=InMemoryAuditSink(),
        operation_repo=ledger,  # type: ignore[arg-type]
        ticket_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )

    skill = PasswordResetSkill()

    # Call 1
    res1 = await skill.run(
        user_id="user-requester-001",
        actor=AUTH_ACTOR,
        user_repo=user_repo,
        write_gateway=executor,
        operation_id="op-reset-1",
    )
    assert res1.success is True
    assert len(cred_service.calls) == 1

    # Call 2: repeats operation, cache hit, credential service NOT called again
    res2 = await skill.run(
        user_id="user-requester-001",
        actor=AUTH_ACTOR,
        user_repo=user_repo,
        write_gateway=executor,
        operation_id="op-reset-1",
    )
    assert res2.success is True
    assert len(cred_service.calls) == 1
