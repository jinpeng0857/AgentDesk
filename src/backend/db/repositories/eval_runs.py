"""Repository for persisted evaluation runs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import EvalRunRecord


class EvalRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self, record: EvalRunRecord) -> EvalRunRecord:
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_runs(self) -> list[EvalRunRecord]:
        query = select(EvalRunRecord).order_by(EvalRunRecord.created_at.desc())
        return list((await self._session.scalars(query)).all())

    async def get_run(self, run_id: str) -> EvalRunRecord | None:
        return await self._session.get(EvalRunRecord, run_id)
