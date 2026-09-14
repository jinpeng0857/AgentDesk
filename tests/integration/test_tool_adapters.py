"""SQL read adapters against real PostgreSQL rows, and fail-closed write adapters."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.integrations.tool_adapters import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledRefundService,
    DisabledTicketService,
    SqlOrderRepository,
    SqlServiceRepository,
    SqlUserRepository,
    WriteCapabilityDisabled,
)
from scripts.seed_demo_data import seed_orders, seed_services, seed_users
from tools.schemas import (
    AccountStatus,
    CheckServiceArgs,
    CreateTicketArgs,
    ProcessRefundArgs,
    QueryOrderArgs,
    QueryUserArgs,
    RequestAccessArgs,
    ResetPasswordArgs,
    ServiceStatus,
)


@pytest.fixture
async def seeded_demo_data(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    async with session_factory() as session:
        await seed_users(session)
        await seed_orders(session)
        await seed_services(session)
        await session.commit()


async def test_query_user_reports_status_and_masked_email(
    session_factory: async_sessionmaker[AsyncSession], seeded_demo_data: None
) -> None:
    async with session_factory() as session:
        result = await SqlUserRepository(session).query_user(
            QueryUserArgs(user_id="user-requester-001")
        )

    assert result.account_status is AccountStatus.ACTIVE
    assert result.masked_email.startswith("r***")
    assert "requester-pass" not in result.masked_email
    assert result.version >= 1


async def test_query_user_rejects_an_unknown_account(
    session_factory: async_sessionmaker[AsyncSession], seeded_demo_data: None
) -> None:
    async with session_factory() as session:
        with pytest.raises(LookupError):
            await SqlUserRepository(session).query_user(
                QueryUserArgs(user_id="user-does-not-exist")
            )


async def test_query_order_returns_a_decimal_refundable_amount(
    session_factory: async_sessionmaker[AsyncSession], seeded_demo_data: None
) -> None:
    async with session_factory() as session:
        result = await SqlOrderRepository(session).query_order(
            QueryOrderArgs(order_id="ORD-1001")
        )

    assert result.requester_id == "user-requester-001"
    assert result.refundable_amount == Decimal("50.00")
    assert isinstance(result.refundable_amount, Decimal)
    assert result.currency == "USD"


async def test_check_service_returns_the_seeded_state(
    session_factory: async_sessionmaker[AsyncSession], seeded_demo_data: None
) -> None:
    async with session_factory() as session:
        result = await SqlServiceRepository(session).check_service(
            CheckServiceArgs(service_name="vpn")
        )

    assert result.status is ServiceStatus.DEGRADED
    assert result.incident_id == "INC-1001"


async def test_check_service_never_fabricates_an_unknown_service(
    session_factory: async_sessionmaker[AsyncSession], seeded_demo_data: None
) -> None:
    async with session_factory() as session:
        with pytest.raises(LookupError):
            await SqlServiceRepository(session).check_service(
                CheckServiceArgs(service_name="not-a-real-service")
            )


@pytest.mark.parametrize(
    ("service", "args"),
    [
        (DisabledCredentialService(), ResetPasswordArgs(user_id="u", account_version=1)),
        (
            DisabledRefundService(),
            ProcessRefundArgs(
                requester_id="u", order_id="ORD-1001", amount=Decimal("1.00"), currency="USD",
                order_version=1,
            ),
        ),
        (
            DisabledTicketService(),
            CreateTicketArgs(
                requester_id="u", title="t", description="d", source_ticket_id="s"
            ),
        ),
        (
            DisabledAccessRequestService(),
            RequestAccessArgs(requester_id="u", resource="r", justification="j"),
        ),
    ],
)
async def test_write_adapters_fail_closed_before_doing_anything(
    service: DisabledCredentialService
    | DisabledRefundService
    | DisabledTicketService
    | DisabledAccessRequestService,
    args: ResetPasswordArgs | ProcessRefundArgs | CreateTicketArgs | RequestAccessArgs,
) -> None:
    method = next(
        getattr(service, name)
        for name in ("reset_password", "process_refund", "create_ticket", "request_access")
        if hasattr(service, name)
    )

    with pytest.raises(WriteCapabilityDisabled):
        await method(args)
