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
from functools import cache
from pathlib import Path

import asyncpg
import pytest
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.auth.passwords import hash_password
from backend.config import AppSettings
from backend.db.models import UserRecord
from backend.db.session import async_session_factory
from backend.jobs.serialization import json_job_deserializer, json_job_serializer
from backend.main import create_app

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


TEST_JWT_SECRET = "integration-test-secret-with-at-least-32-bytes"

REQUESTER_ID = "user-requester-001"
REQUESTER_USERNAME = "requester"
REQUESTER_PASSWORD = "requester-pass"
APPROVER_ID = "user-approver-001"
APPROVER_USERNAME = "approver"
APPROVER_PASSWORD = "approver-pass"
INACTIVE_ID = "user-inactive-001"
INACTIVE_USERNAME = "inactive"
INACTIVE_PASSWORD = "inactive-pass"

FIXTURE_USERS: tuple[tuple[str, str, str, str, bool], ...] = (
    (REQUESTER_ID, REQUESTER_USERNAME, REQUESTER_PASSWORD, "requester", True),
    (APPROVER_ID, APPROVER_USERNAME, APPROVER_PASSWORD, "approver", True),
    (INACTIVE_ID, INACTIVE_USERNAME, INACTIVE_PASSWORD, "requester", False),
)


@cache
def fixture_password_hash(password: str) -> str:
    """Argon2 hashing is intentionally slow, so hash each fixture password once."""
    return hash_password(SecretStr(password))


def build_fixture_users() -> list[UserRecord]:
    return [
        UserRecord(
            id=user_id,
            username=username,
            password_hash=fixture_password_hash(password),
            role=role,
            active=active,
            masked_email=f"{username[0]}***@example.com",
        )
        for user_id, username, password, role, active in FIXTURE_USERS
    ]


@pytest.fixture
async def session_factory(
    migrated_database: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    try:
        yield async_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def seeded_users(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        for record in build_fixture_users():
            await session.merge(record)
        await session.commit()


@pytest.fixture
def api_settings(test_database_url: str) -> AppSettings:
    return AppSettings(
        database_url=test_database_url,
        redis_url="redis://localhost:6379/15",
        jwt_secret=TEST_JWT_SECRET,
        openai_model="test-model",
    )


@pytest.fixture
async def api_app(
    api_settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_users: None,
) -> FastAPI:
    app = create_app(api_settings)
    app.state.session_factory = session_factory
    return app


@pytest.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


TEST_REDIS_URL = "redis://localhost:6379/15"


@pytest.fixture
async def redis_pool() -> AsyncIterator[ArqRedis]:
    pool = await create_pool(
        RedisSettings.from_dsn(TEST_REDIS_URL),
        job_serializer=json_job_serializer,
        job_deserializer=json_job_deserializer,
    )
    try:
        await pool.flushdb()
        yield pool
    finally:
        await pool.aclose()


@pytest.fixture
def login_headers() -> dict[str, str]:
    """Placeholder replaced by :func:`authenticated_client` for real requests."""
    return {}


@pytest.fixture
async def authenticated_client(
    api_client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    login = await api_client.post(
        "/api/auth/login",
        json={"username": REQUESTER_USERNAME, "password": REQUESTER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    api_client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    yield api_client
    api_client.headers.pop("Authorization", None)
