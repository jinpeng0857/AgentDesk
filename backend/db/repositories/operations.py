"""Tool operation ledger persistence and lease handling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ToolOperationRecord


class OperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_idempotency_key(self, idempotency_key: str) -> ToolOperationRecord | None:
        result = await self._session.execute(
            select(ToolOperationRecord).where(
                ToolOperationRecord.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def get_by_operation_id(self, operation_id: str) -> ToolOperationRecord | None:
        result = await self._session.execute(
            select(ToolOperationRecord).where(ToolOperationRecord.operation_id == operation_id)
        )
        return result.scalar_one_or_none()

    async def list_for_ticket(self, ticket_id: uuid.UUID) -> list[ToolOperationRecord]:
        result = await self._session.execute(
            select(ToolOperationRecord)
            .where(ToolOperationRecord.ticket_id == ticket_id)
            .order_by(ToolOperationRecord.created_at, ToolOperationRecord.id)
        )
        return list(result.scalars().all())

    async def create_operation(
        self,
        *,
        ticket_id: uuid.UUID,
        operation_id: str,
        tool_name: str,
        args_hash: str,
        idempotency_key: str,
        status: str = "pending",
        lease_holder: str | None = None,
        lease_duration_seconds: int = 60,
    ) -> ToolOperationRecord:
        now = datetime.now(UTC)
        lease_expires = now + timedelta(seconds=lease_duration_seconds) if lease_holder else None
        record = ToolOperationRecord(
            ticket_id=ticket_id,
            operation_id=operation_id,
            tool_name=tool_name,
            args_hash=args_hash,
            idempotency_key=idempotency_key,
            status=status,
            lease_holder=lease_holder,
            lease_expires_at=lease_expires,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def acquire_lease(
        self,
        idempotency_key: str,
        *,
        lease_holder: str,
        duration_seconds: int = 60,
    ) -> bool:
        now = datetime.now(UTC)
        new_expires = now + timedelta(seconds=duration_seconds)
        stmt = (
            update(ToolOperationRecord)
            .where(
                ToolOperationRecord.idempotency_key == idempotency_key,
                (ToolOperationRecord.lease_holder.is_(None))
                | (ToolOperationRecord.lease_expires_at < now)
                | (ToolOperationRecord.status.in_(("pending", "failed_retryable"))),
            )
            .values(
                lease_holder=lease_holder,
                lease_expires_at=new_expires,
                status="running",
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        rowcount = int(getattr(result, "rowcount", 0) or 0)
        return rowcount > 0

    async def record_success(
        self,
        idempotency_key: str,
        *,
        encrypted_result: str | None = None,
        result_summary: dict[str, object] | None = None,
    ) -> ToolOperationRecord:
        record = await self.get_by_idempotency_key(idempotency_key)
        if record is None:
            raise LookupError(f"operation with key {idempotency_key} not found")
        record.status = "succeeded"
        record.encrypted_result = encrypted_result
        record.result_summary = result_summary
        record.lease_holder = None
        record.lease_expires_at = None
        await self._session.flush()
        return record

    async def record_failure(
        self,
        idempotency_key: str,
        *,
        status: str,
        result_summary: dict[str, object] | None = None,
    ) -> ToolOperationRecord:
        record = await self.get_by_idempotency_key(idempotency_key)
        if record is None:
            raise LookupError(f"operation with key {idempotency_key} not found")
        record.status = status
        record.result_summary = result_summary
        record.lease_holder = None
        record.lease_expires_at = None
        await self._session.flush()
        return record
