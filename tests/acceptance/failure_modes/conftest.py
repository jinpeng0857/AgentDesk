"""PostgreSQL checkpoint fixtures shared by deterministic failure-mode tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from backend.graph.checkpointing import create_postgres_checkpointer
from tests.integration.conftest import to_psycopg_url


@pytest.fixture
async def checkpointer_pool(migrated_database: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = AsyncConnectionPool(
        conninfo=to_psycopg_url(migrated_database),
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool.open()
    try:
        await create_postgres_checkpointer(pool).setup()
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def checkpointer(checkpointer_pool: AsyncConnectionPool) -> AsyncPostgresSaver:
    return create_postgres_checkpointer(checkpointer_pool)
