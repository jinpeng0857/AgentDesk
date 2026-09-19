"""Worker-side execution context.

The context is injected so tests can substitute a deterministic runner and a
stand-in Redis pool without starting the real infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.schemas import TicketStatus
from backend.config import AppSettings


class TicketRunner(Protocol):
    """Runs or resumes the graph for one ticket. Only the Worker may call this."""

    async def run(self, ticket_id: str) -> TicketStatus: ...
    async def resume(self, ticket_id: str, approval_id: str) -> TicketStatus: ...


@dataclass(frozen=True)
class WorkerContext:
    settings: AppSettings
    session_factory: async_sessionmaker[AsyncSession]
    redis: ArqRedis
    runner: TicketRunner
    checkpointer: Any | None = None
