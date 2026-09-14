"""Shared FastAPI dependencies.

Both dependencies resolve from ``app.state`` so importing the application never
requires a live database or Redis connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.errors import ApiError
from backend.config import AppSettings


def get_settings(request: Request) -> AppSettings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, AppSettings):
        raise ApiError(
            status_code=503, code="NOT_READY", message="Application settings are not loaded"
        )
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_factory", None
    )
    if factory is None:
        raise ApiError(
            status_code=503,
            code="NOT_READY",
            message="Database session factory is not available",
        )
    return factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_session_factory(request)() as session:
        yield session


SettingsDep = Annotated[AppSettings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
