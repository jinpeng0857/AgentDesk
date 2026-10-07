"""Live end-to-end evaluation through the production ticket graph.

This module deliberately has no access to expected case labels while it runs a
ticket. Expected values are only copied to the completed artifact after the
graph, tools and PostgreSQL projections have produced their observations.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from psycopg_pool import AsyncConnectionPool
from sqlalchemy import URL, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.context import ModelClient
from agents.schemas import (
    PendingOperation,
    RiskDecision,
    RiskLevel,
    TicketState,
    TicketStatus,
)
from backend.config import AppSettings
from backend.db.models import (
    ApprovalRecord,
    TicketRecord,
    ToolCallRecord,
    ToolOperationRecord,
)
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.tickets import TicketRepository
from backend.governance.executor import ReadToolExecutor
from backend.graph.builder import build_ticket_graph
from backend.graph.checkpointing import create_postgres_checkpointer
from backend.graph.context import GraphContext
from backend.integrations.prompt_loader import LoadedPrompt
from backend.jobs.tickets import WorkerAuditSink, create_default_adapters
from eval.schemas import (
    EvalCaseResult,
    ExpectedHandoff,
    GoldenCase,
    GoldenDataset,
    PolicyDecisionObservation,
    RetrievedReference,
)
from scripts.maintenance.seed_demo_data import (
    seed_knowledge,
    seed_orders,
    seed_services,
    seed_users,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVAL_DATABASE_URL = (
    "postgresql+asyncpg://agentdesk:agentdesk@127.0.0.1:5432/agentdesk_eval_test"
)
EVAL_JWT_SECRET = "agentdesk-live-eval-secret-is-local-and-test-only"
SEMANTIC_NODES = frozenset(
    {
        "supervisor",
        "knowledge",
        "action",
        "policy",
        "approval",
        "action_execute",
        "finalizer",
        "reject",
        "escalate",
    }
)
WRITE_TOOL_NAMES = frozenset(
    {"create_ticket", "request_access", "reset_password", "process_refund"}
)


class LiveEvalConfigurationError(RuntimeError):
    """The requested E2E run is unsafe or its live dependencies are unavailable."""


def database_name(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.database is None:
        raise LiveEvalConfigurationError("evaluation database URL must name a database")
    return parsed.database


def require_isolated_test_database(database_url: str) -> str:
    name = database_name(database_url)
    if not name.endswith("_test"):
        raise LiveEvalConfigurationError(
            f"refusing live evaluation against database {name!r}; database name must end in '_test'"
        )
    return name


def _migration_environment(database_url: str) -> dict[str, str]:
    return {**os.environ, "DATABASE_URL": database_url}


def upgrade_database(database_url: str) -> None:
    """Upgrade the caller-selected isolated database without dropping it."""
    require_isolated_test_database(database_url)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPOSITORY_ROOT,
        env=_migration_environment(database_url),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LiveEvalConfigurationError(
            "Alembic upgrade failed for the isolated evaluation database\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _psycopg_url(database_url: str) -> str:
    parsed = make_url(database_url)
    drivername = "postgresql" if parsed.drivername.startswith("postgresql+") else parsed.drivername
    # ``str(URL)`` intentionally masks the password as ``***``. Checkpoint
    # pools need a real DSN, so render explicitly without masking after the
    # URL has been parsed and reconstructed from the caller-supplied value.
    return URL.create(
        drivername=drivername,
        username=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        database=parsed.database,
        query=parsed.query,
    ).render_as_string(hide_password=False)


def derive_actual_handoffs(node_names: list[str]) -> list[ExpectedHandoff]:
    """Translate observed graph node transitions to the reviewed role-level paths."""
    nodes = [node for node in node_names if node in SEMANTIC_NODES]
    handoffs: list[ExpectedHandoff] = []

    def add(source: str, target: str) -> None:
        handoffs.append(ExpectedHandoff(source=source, target=target))

    for index, source in enumerate(nodes):
        if index + 1 >= len(nodes):
            continue
        target = nodes[index + 1]

        if source == "supervisor":
            route_targets = {
                "knowledge": "knowledge",
                "action": "action_propose",
                "finalizer": "finalize",
                "escalate": "escalated",
            }
            if target in route_targets:
                add("supervisor", route_targets[target])
        elif source == "knowledge":
            route_targets = {"supervisor": "supervisor", "escalate": "escalated"}
            if target in route_targets:
                add("knowledge", route_targets[target])
        elif source == "action":
            if target == "policy":
                add("action_propose", "policy")
            elif target == "action_execute":
                # A read proposal executes internally and returns to Supervisor;
                # Action Execute is intentionally not a logical handoff in the
                # reviewed role-level contract for read tools.
                following = nodes[index + 2] if index + 2 < len(nodes) else None
                add("action_propose", "supervisor" if following == "supervisor" else "escalated")
            elif target == "escalate":
                add("action_propose", "escalated")
            elif target == "finalizer":
                add("action_propose", "finalize")
        elif source == "policy":
            route_targets = {
                "approval": "approval",
                "action_execute": "action_execute",
                "escalate": "escalated",
            }
            if target in route_targets:
                add("policy", route_targets[target])
        elif source == "approval":
            route_targets = {
                "action_execute": "action_execute",
                "reject": "rejected",
                "escalate": "escalated",
            }
            if target in route_targets:
                add("approval", route_targets[target])
        elif source == "action_execute":
            if index > 0 and nodes[index - 1] == "action":
                continue
            route_targets = {
                "supervisor": "supervisor",
                "finalizer": "finalize",
                "escalate": "escalated",
            }
            if target in route_targets:
                add("action_execute", route_targets[target])

    return handoffs


async def _seed_case_data(
    session_factory: async_sessionmaker[AsyncSession],
    case: GoldenCase,
    ticket_id: uuid.UUID,
    trace_id: uuid.UUID,
) -> None:
    async with session_factory() as session:
        await seed_users(session)
        await seed_orders(session)
        await seed_services(session)
        record = TicketRecord(
            id=ticket_id,
            thread_id=str(ticket_id),
            requester_id=case.requester_id,
            user_query=case.query,
            trace_id=trace_id,
            status=TicketStatus.QUEUED.value,
        )
        await TicketRepository(session).add(record)
        await session.commit()


def _state_from_checkpoint(raw: object) -> TicketState | None:
    values = getattr(raw, "values", None)
    if not isinstance(values, Mapping):
        return None
    required = {
        "ticket_id",
        "thread_id",
        "requester_id",
        "user_query",
        "trace_id",
        "status",
        "intent",
        "routing_decision",
        "knowledge_refs",
        "action_proposal",
        "pending_operation",
        "tool_results",
        "risk_decision",
        "approval",
        "agent_history",
        "step_count",
        "resolution_mode",
        "final_response",
    }
    if not required.issubset(values):
        return None
    return cast(TicketState, dict(values))


async def _persist_final_projection(
    session_factory: async_sessionmaker[AsyncSession],
    state: TicketState | None,
) -> None:
    if state is None:
        return
    async with session_factory() as session:
        await TicketRepository(session).sync_projection(state)
        await session.commit()


async def _observe_database(
    session_factory: async_sessionmaker[AsyncSession],
    ticket_id: uuid.UUID,
) -> tuple[
    TicketRecord | None,
    list[ToolCallRecord],
    list[ApprovalRecord],
    list[ToolOperationRecord],
]:
    async with session_factory() as session:
        ticket = await session.get(TicketRecord, ticket_id)
        audits = await AuditRepository(session).list_for_ticket(ticket_id)
        approvals_result = await session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.ticket_id == ticket_id)
            .order_by(ApprovalRecord.created_at, ApprovalRecord.id)
        )
        operations_result = await session.execute(
            select(ToolOperationRecord)
            .where(ToolOperationRecord.ticket_id == ticket_id)
            .order_by(ToolOperationRecord.created_at, ToolOperationRecord.id)
        )
        return (
            ticket,
            audits,
            list(approvals_result.scalars().all()),
            list(operations_result.scalars().all()),
        )


def _is_high_risk_bypass(
    audits: list[ToolCallRecord],
    approvals: list[ApprovalRecord],
    operations: list[ToolOperationRecord],
    decisions: list[PolicyDecisionObservation],
) -> bool:
    high_risk_operation_ids = {
        decision.operation_id
        for decision in decisions
        if decision.risk_level is RiskLevel.HIGH
        and decision.allowed
        and decision.operation_id is not None
    }
    approved_bindings = {
        (approval.operation_id, approval.args_hash)
        for approval in approvals
        if approval.status == "approved"
    }
    operation_hashes = {operation.operation_id: operation.args_hash for operation in operations}
    return any(
        audit.tool_name in WRITE_TOOL_NAMES
        and audit.outcome in ("succeeded", "cache_hit")
        and audit.operation_id in high_risk_operation_ids
        and (audit.operation_id, operation_hashes.get(audit.operation_id or ""))
        not in approved_bindings
        for audit in audits
    )


def _build_case_result(
    *,
    case: GoldenCase,
    state: TicketState | None,
    node_names: list[str],
    policy_decisions: list[PolicyDecisionObservation],
    ticket: TicketRecord | None,
    audits: list[ToolCallRecord],
    approvals: list[ApprovalRecord],
    operations: list[ToolOperationRecord],
    elapsed_ms: float,
    error_code: str | None,
) -> EvalCaseResult:
    references = state.get("knowledge_refs", []) if state is not None else []
    retrieved_references = [
        RetrievedReference(
            document_id=ref.document_id,
            chunk_id=ref.chunk_id,
            title=ref.title,
            content_excerpt=ref.content_excerpt,
            score=ref.score,
        )
        for ref in references
    ]
    risk = state.get("risk_decision") if state is not None else None
    actual_status: TicketStatus | str | None = (
        state.get("status") if state is not None else None
    )
    if actual_status is None and ticket is not None:
        actual_status = ticket.status
    handoffs = derive_actual_handoffs(node_names)
    actual_tools = [audit.tool_name for audit in audits]
    actual_tool_outcomes = [audit.outcome for audit in audits]

    return EvalCaseResult(
        case_id=case.case_id,
        query=case.query,
        expected_intent=case.expected_intent,
        expected_handoffs=case.expected_handoffs,
        expected_tools=case.expected_tools,
        expected_risk=case.expected_risk,
        expected_require_human=case.expected_require_human,
        expected_outcome=case.expected_outcome,
        expected_relevant_document_ids=case.relevant_document_ids,
        observed_graph_nodes=node_names,
        graph_step_count=state.get("step_count") if state is not None else None,
        predicted_intent=state.get("intent") if state is not None else None,
        actual_handoffs=handoffs,
        actual_tools=actual_tools,
        tool_outcomes=actual_tool_outcomes,
        predicted_risk=risk.risk_level if risk is not None else None,
        actual_require_human=risk.require_human if risk is not None else None,
        actual_outcome=actual_status,
        retrieved_document_ids=[ref.document_id for ref in references],
        actual_business_steps=len(audits),
        latency_ms=elapsed_ms,
        high_risk_bypass=_is_high_risk_bypass(
            audits, approvals, operations, policy_decisions
        ),
        policy_decisions=policy_decisions,
        error_code=error_code,
        retrieved_references=retrieved_references,
        failure_code=error_code,
    )


async def run_e2e_candidate(
    *,
    dataset: GoldenDataset,
    model_client: ModelClient,
    prompts: dict[str, LoadedPrompt],
    database_url: str,
    model_name: str,
    base_url: str,
    timeout_s: float = 120.0,
    max_retries: int = 2,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[EvalCaseResult], str]:
    """Run every reviewed case through the real graph and isolated persistence."""
    db_name = require_isolated_test_database(database_url)
    if dataset.level != "full" or dataset.review_status != "reviewed" or len(dataset.cases) != 80:
        raise LiveEvalConfigurationError(
            "e2e-candidate requires the unchanged reviewed full dataset of exactly 80 cases"
        )
    upgrade_database(database_url)

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    checkpoint_pool = AsyncConnectionPool(
        conninfo=_psycopg_url(database_url),
        max_size=8,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    scratch = tempfile.TemporaryDirectory(prefix="agentdesk-live-eval-")
    settings = AppSettings(
        app_env="test",
        database_url=database_url,
        jwt_secret=EVAL_JWT_SECRET,
        openai_base_url=base_url,
        openai_model=model_name,
        openai_think=False,
        openai_timeout_s=timeout_s,
        openai_max_retries=max_retries,
        chroma_path=Path(scratch.name) / "chroma",
    )
    all_results: list[EvalCaseResult] = []
    base_adapters = None
    try:
        await checkpoint_pool.open()
        checkpointer = create_postgres_checkpointer(checkpoint_pool)
        await checkpointer.setup()
        seed_knowledge(Path(scratch.name) / "chroma")
        base_adapters = create_default_adapters(settings)

        for case_index, case in enumerate(dataset.cases, start=1):
            ticket_id = uuid.uuid4()
            trace_id = uuid.uuid4()
            await _seed_case_data(session_factory, case, ticket_id, trace_id)
            reader = ReadToolExecutor(
                adapters=base_adapters,
                audit=WorkerAuditSink(session_factory),
                ticket_id=ticket_id,
                trace_id=trace_id,
            )
            context = GraphContext(
                model_client=model_client,
                prompts=prompts,
                settings=settings,
                tool_executor=reader,
                session_factory=session_factory,
            )
            graph = build_ticket_graph(context, checkpointer=checkpointer)
            config = {"configurable": {"thread_id": str(ticket_id)}}
            node_names: list[str] = []
            decisions: list[PolicyDecisionObservation] = []
            pending_operation_id: str | None = None
            started = time.perf_counter()
            error_code: str | None = None

            async with session_factory() as session:
                ticket = await session.get(TicketRecord, ticket_id)
                if ticket is None:
                    raise RuntimeError(f"evaluation ticket {ticket_id} was not persisted")
                from backend.graph.state import initial_ticket_state

                initial_state = initial_ticket_state(ticket)

            try:
                async for raw_update in graph.astream(
                    initial_state,
                    config=config,
                    stream_mode="updates",
                ):
                    if not isinstance(raw_update, Mapping):
                        continue
                    for raw_node, raw_values in raw_update.items():
                        if not isinstance(raw_node, str):
                            continue
                        node_names.append(raw_node)
                        if raw_node == "action" and isinstance(raw_values, Mapping):
                            raw_operation = raw_values.get("pending_operation")
                            pending_operation_id = (
                                raw_operation.operation_id
                                if isinstance(raw_operation, PendingOperation)
                                else None
                            )
                        if raw_node == "policy" and isinstance(raw_values, Mapping):
                            raw_decision = raw_values.get("risk_decision")
                            if isinstance(raw_decision, RiskDecision):
                                decisions.append(
                                    PolicyDecisionObservation(
                                        risk_level=raw_decision.risk_level,
                                        require_human=raw_decision.require_human,
                                        allowed=raw_decision.allowed,
                                        operation_id=pending_operation_id,
                                    )
                                )
            except Exception as exc:
                # Record a stable class code, never raw exception content which
                # could contain prompt or provider data. Continue the fixed 80.
                error_code = type(exc).__name__

            elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            snapshot = await graph.aget_state(config)
            state = _state_from_checkpoint(snapshot)
            await _persist_final_projection(session_factory, state)
            ticket, audits, approvals, operations = await _observe_database(
                session_factory, ticket_id
            )
            result = _build_case_result(
                case=case,
                state=state,
                node_names=node_names,
                policy_decisions=decisions,
                ticket=ticket,
                audits=audits,
                approvals=approvals,
                operations=operations,
                elapsed_ms=elapsed_ms,
                error_code=error_code,
            )
            all_results.append(result)
            if progress is not None:
                progress(case_index, len(dataset.cases), case.case_id)

        return all_results, db_name
    finally:
        await checkpoint_pool.close()
        await engine.dispose()
        if base_adapters is not None:
            close_client = getattr(base_adapters.knowledge, "close", None)
            if callable(close_client):
                close_client()
        scratch.cleanup()
