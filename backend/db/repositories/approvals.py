"""Approval record persistence, optimistic concurrency control, and expiry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ApprovalRecord


class ApprovalConflictError(RuntimeError):
    """Raised when an approval decision encounters a version conflict or terminal status."""


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, approval_id: uuid.UUID | str) -> ApprovalRecord | None:
        if isinstance(approval_id, str):
            approval_id = uuid.UUID(approval_id)
        result = await self._session.execute(
            select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
        )
        return result.scalar_one_or_none()

    async def get_by_operation_id(self, operation_id: str) -> ApprovalRecord | None:
        result = await self._session.execute(
            select(ApprovalRecord).where(ApprovalRecord.operation_id == operation_id)
        )
        return result.scalar_one_or_none()

    async def list_pending(self) -> list[ApprovalRecord]:
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.status == "pending")
            .order_by(ApprovalRecord.created_at, ApprovalRecord.id)
        )
        return list(result.scalars().all())

    async def list_for_ticket(self, ticket_id: uuid.UUID) -> list[ApprovalRecord]:
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.ticket_id == ticket_id)
            .order_by(ApprovalRecord.created_at, ApprovalRecord.id)
        )
        return list(result.scalars().all())

    async def create_approval(
        self,
        *,
        ticket_id: uuid.UUID,
        operation_id: str,
        tool_name: str,
        risk_level: str,
        args_hash: str,
        args_summary: list[dict[str, object]] | dict[str, object],
        reason_code: str,
        expires_at: datetime,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            ticket_id=ticket_id,
            operation_id=operation_id,
            tool_name=tool_name,
            risk_level=risk_level,
            args_hash=args_hash,
            args_summary=args_summary,
            reason_code=reason_code,
            status="pending",
            version=1,
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def decide(
        self,
        approval_id: uuid.UUID | str,
        *,
        expected_version: int,
        decision: Literal["approve", "reject"],
        approver_id: str,
        reason: str | None = None,
    ) -> ApprovalRecord:
        if isinstance(approval_id, str):
            approval_id = uuid.UUID(approval_id)

        now = datetime.now(UTC)
        new_status = "approved" if decision == "approve" else "rejected"

        stmt = (
            update(ApprovalRecord)
            .where(
                ApprovalRecord.id == approval_id,
                ApprovalRecord.version == expected_version,
                ApprovalRecord.status == "pending",
                ApprovalRecord.expires_at > now,
            )
            .values(
                status=new_status,
                version=ApprovalRecord.version + 1,
                approver_id=approver_id,
                reason=reason,
                decided_at=now,
            )
            .returning(ApprovalRecord)
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is not None:
            await self._session.flush()
            return record

        existing = await self.get_by_id(approval_id)
        if existing is None:
            raise LookupError(f"approval {approval_id} does not exist")
        if existing.status != "pending":
            raise ApprovalConflictError(
                f"approval {approval_id} has already transitioned to {existing.status}"
            )
        if existing.expires_at <= now:
            raise ApprovalConflictError(f"approval {approval_id} has expired")
        if existing.version != expected_version:
            raise ApprovalConflictError(
                f"approval {approval_id} version mismatch: "
                f"expected {expected_version}, got {existing.version}"
            )
        raise ApprovalConflictError(f"unable to decide approval {approval_id}")

    async def expire_pending(self, cutoff: datetime | None = None) -> list[ApprovalRecord]:
        now = cutoff or datetime.now(UTC)
        stmt = (
            update(ApprovalRecord)
            .where(
                ApprovalRecord.status == "pending",
                ApprovalRecord.expires_at <= now,
            )
            .values(status="expired")
            .returning(ApprovalRecord)
        )
        result = await self._session.execute(stmt)
        records = list(result.scalars().all())
        await self._session.flush()
        return records
