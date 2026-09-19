"""LangGraph node closures with strict dependency injection and field ownership."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from langgraph.types import interrupt

from agents.action import create_pending_operation, select_action
from agents.context import ModelCallConfig
from agents.knowledge import ground_knowledge
from agents.policy import WRITE_TOOL_NAMES, assess_risk
from agents.schemas import (
    ActionProposal,
    ActorContext,
    ApprovalSnapshot,
    ApprovalStatus,
    FinalResponse,
    KnowledgeRef,
    PendingOperation,
    ResolutionMode,
    RiskLevel,
    RoutingDecision,
    TicketState,
    TicketStatus,
    ToolOutcome,
)
from agents.supervisor import finalize_ticket, run_supervisor
from backend.db.repositories.approvals import ApprovalRepository
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.executor import ToolAdapters, WriteToolExecutor
from backend.graph.context import GraphContext
from backend.graph.edges import advance_dispatch
from backend.graph.state import validate_node_output_keys
from backend.integrations.prompt_loader import build_system_message
from backend.integrations.tool_adapters import (
    SqlAccessRequestService,
    SqlCredentialService,
    SqlOrderRepository,
    SqlRefundService,
    SqlServiceRepository,
    SqlTicketService,
    SqlUserRepository,
)
from tools.context import KnowledgeStore
from tools.schemas import SearchKnowledgeResult

AsyncNode = Callable[[TicketState], Awaitable[dict[str, Any]]]
SyncNode = Callable[[TicketState], dict[str, Any]]


def make_supervisor_node(context: GraphContext) -> AsyncNode:
    async def supervisor_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["supervisor"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])
        output = await run_supervisor(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
        )
        if not isinstance(output, RoutingDecision):
            raise TypeError(
                f"Supervisor node expected RoutingDecision, got {type(output).__name__}"
            )

        updates = {
            "intent": output.intent,
            "routing_decision": output,
        }
        validate_node_output_keys("supervisor", updates)
        return updates

    return supervisor_node


def make_knowledge_node(context: GraphContext) -> AsyncNode:
    async def knowledge_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["knowledge"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])

        available_refs: list[KnowledgeRef] = []
        if context.tool_executor is not None:
            tool_result = await context.tool_executor.execute(
                agent_id="knowledge",
                tool_name="search_knowledge",
                raw_args={"query": state["user_query"], "top_k": 3},
            )
            if (
                tool_result.outcome is ToolOutcome.SUCCEEDED
                and tool_result.data is not None
            ):
                search_data = cast(SearchKnowledgeResult, tool_result.data)
                available_refs = [
                    KnowledgeRef(
                        document_id=item.document_id,
                        chunk_id=item.chunk_id,
                        title=item.title,
                        content_excerpt=item.content_excerpt,
                        score=item.score,
                    )
                    for item in search_data.refs
                ]

        output = await ground_knowledge(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
            available_refs=available_refs,
        )

        updates = {"knowledge_refs": output.knowledge_refs}
        validate_node_output_keys("knowledge", updates)
        return updates

    return knowledge_node


def make_supervisor_finalizer_node(context: GraphContext) -> AsyncNode:
    async def finalizer_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["supervisor"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])
        output = await finalize_ticket(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
        )
        if isinstance(output, FinalResponse):
            updates = {
                "resolution_mode": output.resolution_mode,
                "final_response": output.final_response,
            }
        else:
            updates = {
                "resolution_mode": ResolutionMode.AUTOMATED,
                "final_response": "Ticket finalized.",
            }

        validate_node_output_keys("supervisor_finalizer", updates)
        return updates

    return finalizer_node


def make_dispatch_node(
    actor: str,
    action: str,
    next_status: TicketStatus | None = None,
) -> SyncNode:
    def dispatch_node(state: TicketState) -> dict[str, Any]:
        next_state = advance_dispatch(
            state,
            actor=cast(Any, actor),
            action=action,
            next_status=next_status,
        )
        updates = {
            "step_count": next_state["step_count"],
            "agent_history": next_state["agent_history"],
            "status": next_state["status"],
        }
        validate_node_output_keys("dispatcher", updates)
        return updates

    return dispatch_node


def make_action_propose_node(context: GraphContext) -> AsyncNode:
    async def action_propose_node(state: TicketState) -> dict[str, Any]:
        existing_op = state.get("pending_operation")
        if existing_op is not None:
            updates: dict[str, Any] = {"pending_operation": existing_op}
            validate_node_output_keys("action_propose", updates)
            return updates

        prompt = context.prompts["action"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(prompt, user_query=state["user_query"])
        proposal = await select_action(
            state=state,
            system_prompt=system_prompt,
            model_client=context.model_client,
            config=config,
        )
        if not isinstance(proposal, ActionProposal):
            raise TypeError(
                f"Action node expected ActionProposal, got {type(proposal).__name__}"
            )

        if proposal.tool_name in WRITE_TOOL_NAMES:
            pending_op: PendingOperation | None = create_pending_operation(proposal)
        else:
            pending_op = None

        updates = {"pending_operation": pending_op}
        validate_node_output_keys("action_propose", updates)
        return updates

    return action_propose_node


def make_policy_node(context: GraphContext) -> AsyncNode:
    async def policy_node(state: TicketState) -> dict[str, Any]:
        pending_op = state.get("pending_operation")
        if pending_op is None:
            raise RuntimeError("Policy node called without pending_operation")

        actor = ActorContext(
            user_id=state["requester_id"],
            role="requester",
            authenticated=True,
        )
        decision = assess_risk(pending_op, actor=actor)
        updates = {"risk_decision": decision}
        validate_node_output_keys("policy", updates)
        return updates

    return policy_node


def make_dispatch_policy_node() -> SyncNode:
    def dispatch_policy_node(state: TicketState) -> dict[str, Any]:
        risk = state.get("risk_decision")
        next_status = (
            TicketStatus.WAITING_APPROVAL
            if (risk is not None and (risk.risk_level is RiskLevel.HIGH or risk.require_human))
            else None
        )
        next_state = advance_dispatch(
            state,
            actor="policy",
            action="evaluate_risk",
            next_status=next_status,
        )
        updates = {
            "step_count": next_state["step_count"],
            "agent_history": next_state["agent_history"],
            "status": next_state["status"],
        }
        validate_node_output_keys("dispatcher", updates)
        return updates

    return dispatch_policy_node


def make_approval_node(context: GraphContext) -> AsyncNode:
    async def approval_node(state: TicketState) -> dict[str, Any]:
        pending_op = state.get("pending_operation")
        if pending_op is None:
            raise RuntimeError("Approval node called without pending_operation")

        approval_id: str | None = None
        if context.session_factory is not None:
            async with context.session_factory() as session:
                repo = ApprovalRepository(session)
                existing = await repo.get_by_operation_id(pending_op.operation_id)
                if existing is None:
                    expires_at = datetime.now(UTC) + timedelta(hours=24)
                    risk_dec = state.get("risk_decision")
                    risk_str = (
                        risk_dec.risk_level.value
                        if risk_dec is not None
                        else "high"
                    )
                    record = await repo.create_approval(
                        ticket_id=uuid.UUID(state["ticket_id"]),
                        operation_id=pending_op.operation_id,
                        tool_name=pending_op.tool_name,
                        risk_level=risk_str,
                        args_hash=pending_op.args_hash,
                        args_summary=pending_op.validated_args.model_dump(mode="json"),
                        reason_code="requires_approval",
                        expires_at=expires_at,
                    )
                    await session.commit()
                    approval_id = str(record.id)
                else:
                    approval_id = str(existing.id)

        # Interrupt execution and wait for human decision
        resume_payload = interrupt(
            {
                "ticket_id": state["ticket_id"],
                "operation_id": pending_op.operation_id,
                "approval_id": approval_id,
            }
        )

        if isinstance(resume_payload, dict) and "approval_id" in resume_payload:
            resumed_approval_id = resume_payload["approval_id"]
        elif approval_id is not None:
            resumed_approval_id = approval_id
        else:
            raise RuntimeError("No approval_id available on resume in approval_node")

        if context.session_factory is not None:
            async with context.session_factory() as session:
                repo = ApprovalRepository(session)
                rec = await repo.get_by_id(resumed_approval_id)
                if rec is None:
                    raise LookupError(f"Approval {resumed_approval_id} not found on resume")
                snapshot = ApprovalSnapshot(
                    approval_id=str(rec.id),
                    operation_id=rec.operation_id,
                    args_hash=rec.args_hash,
                    status=ApprovalStatus(rec.status),
                    version=rec.version,
                    decided_by=rec.approver_id,
                    decided_at=rec.decided_at,
                    expires_at=rec.expires_at,
                )
        else:
            raise RuntimeError("session_factory is required for approval_node")

        updates = {"approval": snapshot}
        validate_node_output_keys("approval", updates)
        return updates

    return approval_node


def make_action_execute_node(context: GraphContext) -> AsyncNode:
    async def action_execute_node(state: TicketState) -> dict[str, Any]:
        pending_op = state.get("pending_operation")
        if pending_op is None:
            raise RuntimeError("Action execute node called without pending_operation")

        ticket_id = uuid.UUID(state["ticket_id"])
        trace_id = uuid.UUID(state["trace_id"])
        approval = state.get("approval")

        if context.session_factory is not None:
            async with context.session_factory() as session:
                adapters_obj = getattr(context.tool_executor, "_adapters", None)
                knowledge_adapter = (
                    getattr(adapters_obj, "knowledge", None)
                    if adapters_obj is not None
                    else cast(Any, None)
                )
                adapters = ToolAdapters(
                    knowledge=cast(KnowledgeStore, knowledge_adapter),
                    user=SqlUserRepository(session),
                    order=SqlOrderRepository(session),
                    service=SqlServiceRepository(session),
                    credential=SqlCredentialService(session),
                    refund=SqlRefundService(session, ticket_id=ticket_id),
                    ticket=SqlTicketService(session, ticket_id=ticket_id),
                    access_request=SqlAccessRequestService(session, ticket_id=ticket_id),
                )
                audit_sink = SqlAuditSink(AuditRepository(session))
                op_repo = OperationRepository(session)
                executor = WriteToolExecutor(
                    adapters=adapters,
                    audit=audit_sink,
                    ticket_id=ticket_id,
                    trace_id=trace_id,
                    operation_repo=op_repo,
                )
                actor = ActorContext(
                    user_id=state["requester_id"],
                    role="requester",
                    authenticated=True,
                )
                tool_result = await executor.execute(
                    agent_id="action",
                    operation=pending_op,
                    actor=actor,
                    approval=approval,
                )
                await session.commit()
        else:
            raise RuntimeError("session_factory is required for action_execute_node")

        results = list(state.get("tool_results", []))
        results.append(tool_result)
        updates = {"tool_results": results}
        validate_node_output_keys("action_execute", updates)
        return updates

    return action_execute_node


def make_dispatch_action_execute_node() -> SyncNode:
    def dispatch_action_execute_node(state: TicketState) -> dict[str, Any]:
        tool_results = state.get("tool_results", [])
        last_result = tool_results[-1] if tool_results else None
        if last_result is not None and str(last_result.outcome) in (
            "timed_out",
            "failed_final",
            "rejected",
            ToolOutcome.TIMED_OUT,
            ToolOutcome.FAILED_FINAL,
            ToolOutcome.REJECTED,
        ):
            next_status = TicketStatus.ESCALATED
        else:
            next_status = TicketStatus.RESOLVED

        next_state = advance_dispatch(
            state,
            actor="action",
            action="execute_operation",
            next_status=next_status,
        )
        updates = {
            "step_count": next_state["step_count"],
            "agent_history": next_state["agent_history"],
            "status": next_state["status"],
        }
        validate_node_output_keys("dispatcher", updates)
        return updates

    return dispatch_action_execute_node

