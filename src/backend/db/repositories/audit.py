"""Tool call audit persistence.

Every attempt, including rejected and timed-out calls, is written here by the
governance executor. Summaries are already redacted before they reach this layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ToolCallRecord


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_tool_call(
        self,
        *,
        request_id: str,
        ticket_id: uuid.UUID,
        trace_id: uuid.UUID,
        operation_id: str | None,
        agent_id: str,
        tool_name: str,
        args_summary: dict[str, object],
        result_summary: dict[str, object] | None,
        outcome: str,
        latency_ms: int,
        retry_count: int = 0,
    ) -> ToolCallRecord:
        record = ToolCallRecord(
            request_id=request_id,
            ticket_id=ticket_id,
            trace_id=trace_id,
            operation_id=operation_id,
            agent_id=agent_id,
            tool_name=tool_name,
            args_summary=args_summary,
            result_summary=result_summary,
            outcome=outcome,
            latency_ms=latency_ms,
            retry_count=retry_count,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_ticket(self, ticket_id: uuid.UUID) -> list[ToolCallRecord]:
        result = await self._session.execute(
            select(ToolCallRecord)
            .where(ToolCallRecord.ticket_id == ticket_id)
            .order_by(ToolCallRecord.timestamp, ToolCallRecord.id)
        )
        return list(result.scalars().all())
