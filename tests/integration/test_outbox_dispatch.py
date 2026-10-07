"""Outbox dispatch publishes deterministic jobs exactly once."""

from __future__ import annotations

import uuid

import pytest
from arq.connections import ArqRedis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import AppSettings
from backend.db.models import JobOutboxRecord, TicketRecord
from backend.db.repositories.outbox import OutboxRepository
from backend.jobs.context import WorkerContext
from backend.jobs.outbox import dispatch_outbox
from backend.jobs.tickets import start_job_id
from tests.integration.conftest import REQUESTER_ID, TEST_JWT_SECRET
from tests.support.fakes.runners import FailingRedis, RecordingTicketRunner


@pytest.fixture(autouse=True)
async def isolated_queue_state(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    """Other modules leave unpublished outbox rows behind; dispatch scans all of them."""
    async with session_factory() as session:
        await session.execute(text("TRUNCATE job_outbox, tickets CASCADE"))
        await session.commit()


def _worker_context(
    session_factory: async_sessionmaker[AsyncSession],
    redis: object,
) -> WorkerContext:
    settings = AppSettings(
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6379/15",
        openai_model="test-model",
    )
    return WorkerContext(
        settings=settings,
        session_factory=session_factory,
        redis=redis,  # type: ignore[arg-type]
        runner=RecordingTicketRunner(),
    )


async def _seed_ticket(
    session_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Create a ticket with its pending outbox row, as the API would."""
    ticket_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            TicketRecord(
                id=ticket_id,
                thread_id=str(ticket_id),
                requester_id=REQUESTER_ID,
                user_query="公司 VPN 无法连接",
                trace_id=uuid.uuid4(),
                status="queued",
                step_count=0,
            )
        )
        await session.flush()
        await OutboxRepository(session).enqueue_start_ticket(
            ticket_id=ticket_id,
            dedupe_key=start_job_id(str(ticket_id)),
        )
        await session.commit()
    return ticket_id


async def _outbox_row(
    session_factory: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID
) -> JobOutboxRecord:
    async with session_factory() as session:
        result = await session.execute(
            select(JobOutboxRecord).where(JobOutboxRecord.ticket_id == ticket_id)
        )
        return result.scalar_one()


async def test_dispatch_publishes_and_marks_the_row(
    session_factory: async_sessionmaker[AsyncSession],
    redis_pool: ArqRedis,
    seeded_users: None,
) -> None:
    ticket_id = await _seed_ticket(session_factory)
    context = _worker_context(session_factory, redis_pool)

    published = await dispatch_outbox({"worker": context})

    assert published == 1
    row = await _outbox_row(session_factory, ticket_id)
    assert row.published_at is not None
    assert row.attempts == 0

    queued = await redis_pool.queued_jobs()
    assert [job.job_id for job in queued] == [start_job_id(str(ticket_id))]
    assert queued[0].function == "start_ticket"
    assert list(queued[0].args) == [str(ticket_id)]


async def test_repeated_dispatch_does_not_create_a_second_job(
    session_factory: async_sessionmaker[AsyncSession],
    redis_pool: ArqRedis,
    seeded_users: None,
) -> None:
    ticket_id = await _seed_ticket(session_factory)
    context = _worker_context(session_factory, redis_pool)

    assert await dispatch_outbox({"worker": context}) == 1
    assert await dispatch_outbox({"worker": context}) == 0

    queued = await redis_pool.queued_jobs()
    assert [job.job_id for job in queued] == [start_job_id(str(ticket_id))]


async def test_redis_failure_records_an_attempt_without_touching_status(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> None:
    ticket_id = await _seed_ticket(session_factory)
    failing = FailingRedis(ConnectionError("redis is down"))
    context = _worker_context(session_factory, failing)

    published = await dispatch_outbox({"worker": context})

    assert published == 0
    row = await _outbox_row(session_factory, ticket_id)
    assert row.published_at is None
    assert row.attempts == 1
    assert row.last_error is not None
    assert row.next_attempt_at is not None

    async with session_factory() as session:
        ticket = await session.get(TicketRecord, ticket_id)
    assert ticket is not None
    assert ticket.status == "queued"
