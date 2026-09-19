"""Ticket projection persistence.

The projection is the API read model. It is written by the Worker from completed
graph state and can be rebuilt from checkpoints, so the API never reads raw
checkpoint blobs.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agents.schemas import TicketState
from backend.db.models import TicketKnowledgeRefRecord, TicketRecord, TraceEventRecord


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: TicketRecord) -> TicketRecord:
        self._session.add(record)
        await self._session.flush()
        return record

    async def get(self, ticket_id: uuid.UUID) -> TicketRecord | None:
        return await self._session.get(TicketRecord, ticket_id)

    async def get_for_requester(
        self, ticket_id: uuid.UUID, requester_id: str
    ) -> TicketRecord | None:
        result = await self._session.execute(
            select(TicketRecord).where(
                TicketRecord.id == ticket_id,
                TicketRecord.requester_id == requester_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_requester(
        self, requester_id: str, *, limit: int = 50
    ) -> list[TicketRecord]:
        result = await self._session.execute(
            select(TicketRecord)
            .where(TicketRecord.requester_id == requester_id)
            .order_by(TicketRecord.created_at.desc(), TicketRecord.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_knowledge_refs(
        self, ticket_id: uuid.UUID
    ) -> list[TicketKnowledgeRefRecord]:
        result = await self._session.execute(
            select(TicketKnowledgeRefRecord)
            .where(TicketKnowledgeRefRecord.ticket_id == ticket_id)
            .order_by(TicketKnowledgeRefRecord.position)
        )
        return list(result.scalars().all())

    async def sync_projection(self, state: TicketState) -> None:
        """Upsert ticket fields, citations and newly observed agent steps together."""
        ticket_id = uuid.UUID(state["ticket_id"])
        record = await self._session.get(TicketRecord, ticket_id, with_for_update=True)
        if record is None:
            raise LookupError(f"ticket {ticket_id} disappeared before projection sync")

        record.status = str(state["status"])
        record.step_count = state["step_count"]
        record.intent = str(state["intent"]) if state["intent"] is not None else None
        record.resolution_mode = (
            str(state["resolution_mode"]) if state["resolution_mode"] is not None else None
        )
        record.final_response = state["final_response"]
        risk = state["risk_decision"]
        record.risk_level = str(risk.risk_level) if risk is not None else None

        for position, ref in enumerate(state["knowledge_refs"]):
            statement = (
                pg_insert(TicketKnowledgeRefRecord)
                .values(
                    ticket_id=ticket_id,
                    position=position,
                    document_id=ref.document_id,
                    chunk_id=ref.chunk_id,
                    title=ref.title,
                    content_excerpt=ref.content_excerpt,
                    score=ref.score,
                )
                .on_conflict_do_update(
                    index_elements=[
                        TicketKnowledgeRefRecord.ticket_id,
                        TicketKnowledgeRefRecord.chunk_id,
                    ],
                    set_={"position": position},
                )
            )
            await self._session.execute(statement)

        for step in state["agent_history"]:
            event = (
                pg_insert(TraceEventRecord)
                .values(
                    request_id=f"step-{step.step}",
                    ticket_id=ticket_id,
                    trace_id=uuid.UUID(state["trace_id"]),
                    actor=step.actor,
                    event_type="agent_step",
                    summary=step.action,
                    timestamp=step.timestamp,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        TraceEventRecord.ticket_id,
                        TraceEventRecord.request_id,
                    ]
                )
            )
            await self._session.execute(event)

        await self._session.flush()

    async def list_trace_events(
        self, ticket_id: uuid.UUID
    ) -> list[TraceEventRecord]:
        result = await self._session.execute(
            select(TraceEventRecord)
            .where(TraceEventRecord.ticket_id == ticket_id)
            .order_by(TraceEventRecord.timestamp.asc(), TraceEventRecord.request_id.asc())
        )
        return list(result.scalars().all())
