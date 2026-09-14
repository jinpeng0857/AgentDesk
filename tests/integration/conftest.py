"""Fixtures for integration tests that require real PostgreSQL.

The destructive setup below drops and recreates a dedicated database. It
refuses to run unless the target database name ends with ``_test`` so a
misconfigured ``TEST_DATABASE_URL`` cannot wipe a development database.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk_test"
)


def database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def to_psycopg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def to_maintenance_url(url: str) -> str:
    base, _, _name = to_psycopg_url(url).rpartition("/")
    return f"{base}/postgres"


async def _recreate_database(url: str) -> None:
    name = database_name(url)
    connection = await asyncpg.connect(to_maintenance_url(url))
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


def _run_alembic_upgrade(url: str) -> None:
    environment = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "alembic upgrade head failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    name = database_name(url)
    if not name.endswith("_test"):
        pytest.exit(
            f"refusing destructive integration setup: database {name!r} does not end with '_test'",
            returncode=1,
        )
    return url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> Iterator[str]:
    """Recreate the dedicated test database and apply every migration."""
    asyncio.run(_recreate_database(test_database_url))
    _run_alembic_upgrade(test_database_url)
    yield test_database_url


@pytest.fixture
async def migrated_connection(
    migrated_database: str,
) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()
