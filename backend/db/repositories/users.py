"""User persistence and lookup."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import UserRecord


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> UserRecord | None:
        result = await self._session.execute(
            select(UserRecord).where(UserRecord.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        return await self._session.get(UserRecord, user_id)

    async def add(self, record: UserRecord) -> UserRecord:
        self._session.add(record)
        await self._session.flush()
        return record
