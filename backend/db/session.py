"""Async engine and session factories.

This module only defines factories. No engine, pool or connection is created at
import time, so importing it is safe for OpenAPI generation and unit tests.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import AppSettings


def create_async_engine_from_settings(settings: AppSettings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
