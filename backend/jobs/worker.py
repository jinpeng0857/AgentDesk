"""ARQ worker configuration.

The worker is the only process allowed to start or resume the graph.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from arq import cron
from arq.connections import RedisSettings, create_pool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from backend.config import AppSettings
from backend.db.session import async_session_factory, create_async_engine_from_settings
from backend.integrations.model_client import OpenAICompatibleModelClient
from backend.integrations.prompt_loader import load_all_prompts
from backend.jobs.context import WorkerContext
from backend.jobs.outbox import dispatch_outbox
from backend.jobs.serialization import json_job_deserializer, json_job_serializer
from backend.jobs.tickets import GraphTicketRunner, resume_ticket, start_ticket

OUTBOX_POLL_SECONDS = 5


async def startup(ctx: dict[str, Any]) -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    settings = AppSettings()
    settings.validate_readiness(require_model=True)
    engine = create_async_engine_from_settings(settings)
    session_factory = async_session_factory(engine)
    redis = await create_pool(
        RedisSettings.from_dsn(settings.redis_url),
        job_serializer=json_job_serializer,
        job_deserializer=json_job_deserializer,
    )

    checkpointer_pool = AsyncConnectionPool(
        conninfo=settings.checkpoint_database_url,
        max_size=20,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await checkpointer_pool.open()
    checkpointer = AsyncPostgresSaver(checkpointer_pool)

    model_client = OpenAICompatibleModelClient.from_settings(settings)
    prompts = load_all_prompts()

    runner = GraphTicketRunner(
        session_factory=session_factory,
        checkpointer=checkpointer,
        model_client=model_client,
        prompts=prompts,
        settings=settings,
    )

    ctx["engine"] = engine
    ctx["checkpointer_pool"] = checkpointer_pool
    ctx["worker"] = WorkerContext(
        settings=settings,
        session_factory=session_factory,
        redis=redis,
        runner=runner,
        checkpointer=checkpointer,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    context = ctx.get("worker")
    if isinstance(context, WorkerContext):
        await context.redis.aclose()
    checkpointer_pool = ctx.get("checkpointer_pool")
    if checkpointer_pool is not None:
        await checkpointer_pool.close()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    functions = [start_ticket, resume_ticket]
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
