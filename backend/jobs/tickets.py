"""Ticket jobs executed by the ARQ worker."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.context import ModelClient
from agents.schemas import TicketStatus
from backend.config import AppSettings
from backend.db.models import TicketRecord
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.tickets import TicketRepository
from backend.governance.audit import ToolAttempt
from backend.governance.executor import ReadToolExecutor, ToolAdapters
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.graph.state import initial_ticket_state
from backend.integrations.knowledge_store import ChromaKnowledgeStore
from backend.integrations.prompt_loader import LoadedPrompt
from backend.integrations.tool_adapters import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledRefundService,
    DisabledTicketService,
)
from backend.jobs.context import WorkerContext

START_TICKET_JOB = "start_ticket"
RESUME_TICKET_JOB = "resume_ticket"


def start_job_id(ticket_id: str) -> str:
    """Deterministic job ID: repeat delivery cannot create a second logical run."""
    return f"start:{ticket_id}"


def resume_job_id(approval_id: str, version: int) -> str:
    """Deterministic job ID: repeat delivery cannot create a second logical resume run."""
    return f"resume:{approval_id}:{version}"


def worker_context(ctx: dict[str, Any]) -> WorkerContext:
    context = ctx.get("worker")
    if not isinstance(context, WorkerContext):
        raise RuntimeError("worker context has not been initialised")
    return context


class WorkerAuditSink:
    """Audit sink that persists tool call attempts via session_factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, attempt: ToolAttempt) -> None:
        async with self._session_factory() as session:
            repo = AuditRepository(session)
            await repo.record_tool_call(
                request_id=attempt.request_id,
                ticket_id=attempt.ticket_id,
                trace_id=attempt.trace_id,
                operation_id=attempt.operation_id,
                agent_id=attempt.agent_id,
                tool_name=attempt.tool_name,
                args_summary=attempt.args_summary,
                result_summary=attempt.result_summary,
                outcome=str(attempt.outcome),
                latency_ms=attempt.latency_ms,
                retry_count=attempt.retry_count,
            )
            await session.commit()


def create_default_adapters(
    settings: AppSettings,
) -> ToolAdapters:
    return ToolAdapters(
        knowledge=ChromaKnowledgeStore(settings.chroma_path),
        user=cast(Any, None),
        order=cast(Any, None),
        service=cast(Any, None),
        credential=DisabledCredentialService(),
        refund=DisabledRefundService(),
        ticket=DisabledTicketService(),
        access_request=DisabledAccessRequestService(),
    )


class GraphTicketRunner:
    """Executes or resumes the LangGraph ticket workflow for a given ticket.

    Only the Worker process may call this runner.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        checkpointer: BaseCheckpointSaver,
        graph: CompiledStateGraph | None = None,
        graph_factory: Callable[[TicketRecord], CompiledStateGraph] | None = None,
        model_client: ModelClient | None = None,
        prompts: dict[str, LoadedPrompt] | None = None,
        settings: AppSettings | None = None,
        adapters_factory: Callable[[AppSettings], ToolAdapters] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._checkpointer = checkpointer
        self._graph = graph
        self._graph_factory = graph_factory
        self._model_client = model_client
        self._prompts = prompts
        self._settings = settings
        self._adapters_factory = adapters_factory

    async def run(self, ticket_id: str | uuid.UUID) -> TicketStatus:
        ticket_uuid = uuid.UUID(str(ticket_id))
        thread_id = str(ticket_uuid)

        async with self._session_factory() as session:
            repo = TicketRepository(session)
            record = await repo.get(ticket_uuid)
            if record is None:
                raise LookupError(f"Ticket {ticket_id} does not exist")

            current_status = TicketStatus(record.status)
            if current_status in (
                TicketStatus.RESOLVED,
                TicketStatus.ESCALATED,
                TicketStatus.REJECTED,
                TicketStatus.FAILED,
                TicketStatus.EXPIRED,
            ):
                return current_status

        config = {"configurable": {"thread_id": thread_id}}
        checkpoint = await self._checkpointer.aget(config)

        input_state = None if checkpoint is not None else initial_ticket_state(record)

        if self._graph is not None:
            graph = self._graph
        elif self._graph_factory is not None:
            graph = self._graph_factory(record)
        else:
            graph = self._build_default_graph(record)

        final_status = current_status
        async for state in graph.astream(input_state, config=config, stream_mode="values"):
            async with self._session_factory() as session:
                repo = TicketRepository(session)
                await repo.sync_projection(state)
                await session.commit()
            final_status = TicketStatus(state["status"])

        return final_status

    async def resume(
        self, ticket_id: str | uuid.UUID, approval_id: str | uuid.UUID
    ) -> TicketStatus:
        ticket_uuid = uuid.UUID(str(ticket_id))
        thread_id = str(ticket_uuid)

        async with self._session_factory() as session:
            repo = TicketRepository(session)
            record = await repo.get(ticket_uuid)
            if record is None:
                raise LookupError(f"Ticket {ticket_id} does not exist")

            current_status = TicketStatus(record.status)
            if current_status in (
                TicketStatus.RESOLVED,
                TicketStatus.ESCALATED,
                TicketStatus.REJECTED,
                TicketStatus.FAILED,
                TicketStatus.EXPIRED,
            ):
                return current_status

        config = {"configurable": {"thread_id": thread_id}}

        if self._graph is not None:
            graph = self._graph
        elif self._graph_factory is not None:
            graph = self._graph_factory(record)
        else:
            graph = self._build_default_graph(record)

        resume_command = Command(resume={"approval_id": str(approval_id)})
        final_status = current_status
        async for state in graph.astream(resume_command, config=config, stream_mode="values"):
            async with self._session_factory() as session:
                repo = TicketRepository(session)
                await repo.sync_projection(state)
                await session.commit()
            final_status = TicketStatus(state["status"])

        return final_status

    def _build_default_graph(self, record: TicketRecord) -> CompiledStateGraph:
        if self._model_client is None or self._prompts is None or self._settings is None:
            raise RuntimeError(
                "Cannot build default graph: model_client, prompts, or settings not configured"
            )

        if self._adapters_factory is not None:
            adapters = self._adapters_factory(self._settings)
        else:
            adapters = create_default_adapters(self._settings)

        audit_sink = WorkerAuditSink(self._session_factory)
        tool_executor = ReadToolExecutor(
            adapters=adapters,
            audit=audit_sink,
            ticket_id=record.id,
            trace_id=record.trace_id,
        )
        context = GraphContext(
            model_client=self._model_client,
            prompts=self._prompts,
            settings=self._settings,
            tool_executor=tool_executor,
            session_factory=self._session_factory,
        )
        return build_ticket_graph(context, checkpointer=self._checkpointer)


async def start_ticket(ctx: dict[str, Any], ticket_id: str) -> None:
    await worker_context(ctx).runner.run(ticket_id)


async def resume_ticket(ctx: dict[str, Any], ticket_id: str, approval_id: str) -> None:
    await worker_context(ctx).runner.resume(ticket_id, approval_id)
