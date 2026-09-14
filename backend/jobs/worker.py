"""ARQ worker configuration.

The worker is the only process allowed to start or resume the graph. In this
phase the ticket runner is a fail-closed placeholder: the LangGraph runtime and
its process containers are wired in the graph task. Everything else here — JSON
payloads, deterministic job IDs and outbox polling — is final.
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings, create_pool

from agents.schemas import TicketStatus
from backend.config import AppSettings
from backend.db.session import async_session_factory, create_async_engine_from_settings
from backend.jobs.context import WorkerContext
from backend.jobs.outbox import dispatch_outbox
from backend.jobs.serialization import json_job_deserializer, json_job_serializer
from backend.jobs.tickets import start_ticket

OUTBOX_POLL_SECONDS = 5


class UnavailableTicketRunner:
    """Fail-closed stand-in used until the graph runtime is connected."""

    async def run(self, ticket_id: str) -> TicketStatus:
        raise RuntimeError(
            f"ticket runner is not wired yet; cannot run ticket {ticket_id}"
        )


async def startup(ctx: dict[str, Any]) -> None:
    settings = AppSettings()
    settings.validate_readiness(require_model=True)
    engine = create_async_engine_from_settings(settings)
    redis = await create_pool(
        RedisSettings.from_dsn(settings.redis_url),
        job_serializer=json_job_serializer,
        job_deserializer=json_job_deserializer,
    )
    ctx["engine"] = engine
    ctx["worker"] = WorkerContext(
        settings=settings,
        session_factory=async_session_factory(engine),
        redis=redis,
        runner=UnavailableTicketRunner(),
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    context = ctx.get("worker")
    if isinstance(context, WorkerContext):
        await context.redis.aclose()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    functions = [start_ticket]
    cron_jobs = [
        cron(
            dispatch_outbox,
            second=set(range(0, 60, OUTBOX_POLL_SECONDS)),
            unique=True,
        )
    ]
    on_startup = startup
    on_shutdown = shutdown
    job_serializer = staticmethod(json_job_serializer)
    job_deserializer = staticmethod(json_job_deserializer)
    max_tries = 3
