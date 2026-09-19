"""Transactional outbox for queue publishing.

A ticket and its outbox row are committed together, so a crash between the two
cannot leave an unexplained ``queued`` ticket. Publishing updates the outbox row
only after ARQ accepted or already owned the deterministic job ID.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import JobOutboxRecord

START_TICKET_JOB = "start_ticket"
RESUME_TICKET_JOB = "resume_ticket"


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue_start_ticket(
        self, *, ticket_id: uuid.UUID, dedupe_key: str
    ) -> JobOutboxRecord:
        record = JobOutboxRecord(
            ticket_id=ticket_id,
            job_name=START_TICKET_JOB,
            payload={"ticket_id": str(ticket_id)},
            dedupe_key=dedupe_key,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def enqueue_resume_ticket(
        self, *, ticket_id: uuid.UUID, approval_id: uuid.UUID, dedupe_key: str
    ) -> JobOutboxRecord:
        record = JobOutboxRecord(
            ticket_id=ticket_id,
            job_name=RESUME_TICKET_JOB,
            payload={"ticket_id": str(ticket_id), "approval_id": str(approval_id)},
            dedupe_key=dedupe_key,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def claim_batch(self, *, limit: int = 20) -> list[JobOutboxRecord]:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(JobOutboxRecord)
            .where(
                JobOutboxRecord.published_at.is_(None),
                or_(
                    JobOutboxRecord.next_attempt_at.is_(None),
                    JobOutboxRecord.next_attempt_at <= now,
                ),
            )
            .order_by(JobOutboxRecord.created_at, JobOutboxRecord.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def mark_published(self, record: JobOutboxRecord) -> None:
        record.published_at = datetime.now(UTC)
        record.last_error = None
        await self._session.flush()

    async def mark_failed(
        self, record: JobOutboxRecord, *, error: str, retry_delay_s: int
    ) -> None:
        record.attempts += 1
        record.last_error = error[:1_000]
        record.next_attempt_at = datetime.now(UTC) + timedelta(seconds=retry_delay_s)
        await self._session.flush()
