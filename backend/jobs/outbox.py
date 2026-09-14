"""Outbox dispatch: publish committed tickets to the ARQ queue.

The row lock taken by ``claim_batch`` is held while enqueueing so two workers
cannot publish the same ticket. A Redis failure records an attempt and a retry
time; the ticket status is never changed here.
"""

from __future__ import annotations

from typing import Any

from redis.exceptions import RedisError

from backend.db.repositories.outbox import OutboxRepository
from backend.jobs.tickets import START_TICKET_JOB, start_job_id, worker_context

OUTBOX_BATCH_SIZE = 20
OUTBOX_RETRY_DELAY_S = 15


async def dispatch_outbox(ctx: dict[str, Any]) -> int:
    context = worker_context(ctx)
    published = 0

    async with context.session_factory() as session:
        repository = OutboxRepository(session)
        rows = await repository.claim_batch(limit=OUTBOX_BATCH_SIZE)

        for row in rows:
            try:
                await context.redis.enqueue_job(
                    START_TICKET_JOB,
                    str(row.ticket_id),
                    _job_id=start_job_id(str(row.ticket_id)),
                )
            except (RedisError, OSError, TimeoutError) as exc:
                await repository.mark_failed(
                    row,
                    error=f"{type(exc).__name__}: {exc}",
                    retry_delay_s=OUTBOX_RETRY_DELAY_S,
                )
            else:
                # enqueue_job returns None when the deterministic job already
                # exists; that is a successful publish, not a failure.
                await repository.mark_published(row)
                published += 1

        await session.commit()

    return published
