"""Live end-to-end evaluation through the production ticket graph.

This module deliberately has no access to expected case labels while it runs a
ticket. Expected values are only copied to the completed artifact after the
graph, tools and PostgreSQL projections have produced their observations.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
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
    RoutingDecision,
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
from backend.integrations.model_client import ModelConfigurationError, ModelOutputInvalid
from backend.integrations.prompt_loader import LoadedPrompt
from backend.jobs.tickets import GraphTicketRunner, WorkerAuditSink, create_default_adapters
from eval.schemas import (
    EvalCaseResult,
    ExpectedHandoff,
    GoldenCase,
    GoldenDataset,
    PolicyDecisionObservation,
    RetrievedReference,
    RoutingDecisionObservation,
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
        "reject_request",
        "escalate",
    }
)
WRITE_TOOL_NAMES = frozenset(
    {"create_ticket", "request_access", "reset_password", "process_refund"}
)
MAX_E2E_CONCURRENCY = 8


class _CaseReadWriteLock:
    """Allow read-only cases to overlap while isolating shared-fixture mutations."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_readers = 0
        self._waiting_writers = 0
        self._writer_active = False

    @asynccontextmanager
    async def read(self) -> AsyncIterator[None]:
        async with self._condition:
            await self._condition.wait_for(
                lambda: not self._writer_active and self._waiting_writers == 0
            )
            self._active_readers += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active_readers -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def write(self) -> AsyncIterator[None]:
        async with self._condition:
            self._waiting_writers += 1
            self._condition.notify_all()
            try:
                await self._condition.wait_for(
                    lambda: not self._writer_active and self._active_readers == 0
                )
            except BaseException:
                self._waiting_writers -= 1
                self._condition.notify_all()
                raise
            self._waiting_writers -= 1
            self._writer_active = True
        try:
            yield
        finally:
            async with self._condition:
                self._writer_active = False
                self._condition.notify_all()


class LiveEvalConfigurationError(RuntimeError):
    """The requested E2E run is unsafe or its live dependencies are unavailable."""


E2E_CASE_BANDS: dict[str, tuple[int, int | None]] = {
    "development": (1, 19),
    "smoke": (20, 79),
    "full": (80, None),
}


def e2e_case_band(level: str) -> tuple[int, int | None]:
    """Return the inclusive case-count band a declared dataset level must satisfy."""
    try:
        return E2E_CASE_BANDS[level]
    except KeyError:
        raise LiveEvalConfigurationError(
            "unsupported dataset level "
            + repr(level)
            + "; expected one of "
            + repr(sorted(E2E_CASE_BANDS))
        ) from None


def validate_e2e_dataset(dataset: GoldenDataset) -> None:
    """Reject unreviewed or mis-levelled datasets from the live E2E stage.

    The gate binds the declared ``level`` to its case-count band instead of a
    fixed count. A reviewed 20-case smoke set may run the real graph, but the
    same 20 cases labelled ``full`` are still refused, and the artifact records
    ``dataset_level`` so a smoke run can never be read as a full acceptance.
    """
    if dataset.review_status != "reviewed":
        raise LiveEvalConfigurationError(
            "e2e-candidate requires a human-reviewed dataset (review_status=reviewed)"
        )
    low, high = e2e_case_band(dataset.level)
    count = len(dataset.cases)
    if count < low:
        raise LiveEvalConfigurationError(
            f"e2e-candidate requires a reviewed {dataset.level} dataset with at "
            f"least {low} cases; got {count}"
        )
    if high is not None and count > high:
        raise LiveEvalConfigurationError(
            f"e2e-candidate requires a reviewed {dataset.level} dataset with at "
            f"most {high} cases; got {count}"
        )


def _format_e2e_error(exc: Exception, node_names: list[str]) -> str:
    """Return a safe, stable diagnostic code without model/provider payloads."""
    if isinstance(exc, ModelOutputInvalid):
        code = (
            f"ModelOutputInvalid:{exc.output_model}:"
            f"repairs={exc.repair_attempts}:validation={exc.validation_summary}"
        )
    elif isinstance(exc, ModelConfigurationError):
        code = f"ModelConfigurationError:{exc.reason_code}"
    else:
        code = type(exc).__name__
    return f"{code}:after={node_names[-1]}" if node_names else code


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
    nodes = [
        "approval" if node == "__interrupt__" else node
        for node in node_names
        if node in SEMANTIC_NODES or node == "__interrupt__"
    ]
    handoffs: list[ExpectedHandoff] = []

    def add(source: str, target: str) -> None:
        handoffs.append(ExpectedHandoff(source=source, target=target))

    for index, source in enumerate(nodes):
        if index + 1 >= len(nodes):
            continue
        target = nodes[index + 1]
        # The reviewed role vocabulary has no separate reject role: a
        # deterministic rejection is a terminal finalization. Normalizing the
        # node name keeps the observed path comparable with the reviewed labels.
        if target == "reject_request":
            target = "finalizer"

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


async def _seed_shared_fixtures(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_users(session)
        await seed_orders(session)
        await seed_services(session)
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


def _retrieved_document_ids_from_audits(audits: list[ToolCallRecord]) -> list[str]:
    """Read actual governed search IDs from the safe tool audit projection."""
    document_ids: list[str] = []
    for audit in audits:
        if audit.tool_name != "search_knowledge" or audit.outcome not in (
            "succeeded",
            "cache_hit",
        ):
            continue
        summary = audit.result_summary
        raw_ids = summary.get("document_ids") if summary is not None else None
        if isinstance(raw_ids, list):
            document_ids.extend(item for item in raw_ids if isinstance(item, str))
    return list(dict.fromkeys(document_ids))


def _build_case_result(
    *,
    case: GoldenCase,
    state: TicketState | None,
    node_names: list[str],
    policy_decisions: list[PolicyDecisionObservation],
    routing_decisions: list[RoutingDecisionObservation],
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
    executed_operation_ids = {
        audit.operation_id for audit in audits if audit.operation_id is not None
    }
    for approval in approvals:
        if approval.operation_id not in executed_operation_ids:
            actual_tools.append(approval.tool_name)
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
        retrieved_document_ids=_retrieved_document_ids_from_audits(audits),
        actual_business_steps=len(audits),
        latency_ms=elapsed_ms,
        high_risk_bypass=_is_high_risk_bypass(
            audits, approvals, operations, policy_decisions
        ),
        policy_decisions=policy_decisions,
        routing_decisions=routing_decisions,
        tool_error_codes=[
            result.error_code
            for result in state.get("tool_results", [])
            if result.error_code is not None
        ]
        if state is not None
        else [],
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
    concurrency: int = 4,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[EvalCaseResult], str, int]:
    """Run the reviewed cases with bounded parallelism and isolated shared fixtures."""
    db_name = require_isolated_test_database(database_url)
    validate_e2e_dataset(dataset)
    if not 1 <= concurrency <= MAX_E2E_CONCURRENCY:
        raise LiveEvalConfigurationError(
            f"E2E concurrency must be between 1 and {MAX_E2E_CONCURRENCY}"
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
    base_adapters = None
    try:
        await checkpoint_pool.open()
        checkpointer = create_postgres_checkpointer(checkpoint_pool)
        await checkpointer.setup()
        seed_knowledge(Path(scratch.name) / "chroma")
        base_adapters = create_default_adapters(settings)

        # Shared demo identities/orders/services are initialized once. Mutating
        # cases take an exclusive gate and restore the fixtures before release;
        # read-only cases may overlap without observing another case's writes.
        await _seed_shared_fixtures(session_factory)
        slots = asyncio.Semaphore(concurrency)
        fixture_gate = _CaseReadWriteLock()
        active_cases = 0
        max_active_cases = 0

        async def evaluate_case(
            case_index: int, case: GoldenCase
        ) -> EvalCaseResult:
            nonlocal active_cases, max_active_cases
            mutating_case = any(str(tool) in WRITE_TOOL_NAMES for tool in case.expected_tools)
            gate = fixture_gate.write() if mutating_case else fixture_gate.read()
            async with slots, gate:
                if mutating_case:
                    await _seed_shared_fixtures(session_factory)
                active_cases += 1
                max_active_cases = max(max_active_cases, active_cases)
                try:
                    result = await evaluate_one_case(case_index, case)
                finally:
                    active_cases -= 1
                    if mutating_case:
                        await _seed_shared_fixtures(session_factory)
                return result

        async def evaluate_one_case(
            case_index: int, case: GoldenCase
        ) -> EvalCaseResult:
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
            routing_decisions: list[RoutingDecisionObservation] = []
            pending_operation_id: str | None = None
            pending_tool_name: str | None = None
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
                            pending_tool_name = (
                                raw_operation.tool_name
                                if isinstance(raw_operation, PendingOperation)
                                else None
                            )
                        if raw_node == "supervisor" and isinstance(raw_values, Mapping):
                            raw_route = raw_values.get("routing_decision")
                            if isinstance(raw_route, RoutingDecision):
                                routing_decisions.append(
                                    RoutingDecisionObservation(
                                        intent=raw_route.intent,
                                        next_agent=raw_route.next_agent,
                                        confidence=raw_route.confidence,
                                        escalate=raw_route.escalate,
                                        reason_code=raw_route.reason_code.value,
                                    )
                                )
                        if (
                            raw_node == "policy"
                            and isinstance(raw_values, Mapping)
                            and pending_tool_name is not None
                        ):
                            raw_decision = raw_values.get("risk_decision")
                            if isinstance(raw_decision, RiskDecision):
                                decisions.append(
                                    PolicyDecisionObservation(
                                        tool_name=pending_tool_name,
                                        risk_level=raw_decision.risk_level,
                                        require_human=raw_decision.require_human,
                                        allowed=raw_decision.allowed,
                                        operation_id=pending_operation_id,
                                        reason_code=raw_decision.reason_code.value,
                                    )
                                )
            except Exception as exc:
                # Record a stable class code, never raw exception content which
                # could contain prompt or provider data. Continue the fixed 80.
                error_code = _format_e2e_error(exc, node_names)
                runner = GraphTicketRunner(
                    session_factory=session_factory,
                    checkpointer=checkpointer,
                    graph=graph,
                )
                await runner.fail(ticket_id, error_code)

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
                routing_decisions=routing_decisions,
                ticket=ticket,
                audits=audits,
                approvals=approvals,
                operations=operations,
                elapsed_ms=elapsed_ms,
                error_code=error_code,
            )
            if progress is not None:
                progress(case_index, len(dataset.cases), case.case_id)
            return result

        all_results = await asyncio.gather(
            *(
                evaluate_case(case_index, case)
                for case_index, case in enumerate(dataset.cases, start=1)
            )
        )
        return list(all_results), db_name, max_active_cases
    finally:
        await checkpoint_pool.close()
        await engine.dispose()
        if base_adapters is not None:
            close_client = getattr(base_adapters.knowledge, "close", None)
            if callable(close_client):
                close_client()
        scratch.cleanup()
