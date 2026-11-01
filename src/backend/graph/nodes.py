"""LangGraph node closures with strict dependency injection and field ownership."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from agents.action import (
    ActionProposalValidationError,
    create_pending_operation,
    select_action,
)
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
    ResourceContext,
    RiskLevel,
    RoutingDecision,
    TicketState,
    TicketStatus,
    ToolOutcome,
    ToolResult,
)
from agents.supervisor import finalize_ticket, run_supervisor
from backend.db.models import OrderRecord, UserRecord
from backend.db.repositories.approvals import ApprovalRepository
from backend.db.repositories.audit import AuditRepository
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import SqlAuditSink
from backend.governance.executor import ReadToolExecutor, ToolAdapters, WriteToolExecutor
from backend.graph.context import GraphContext
from backend.graph.edges import advance_dispatch
from backend.graph.state import validate_node_output_keys
from backend.integrations.model_client import ModelOutputInvalid
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
from tools.schemas import ProcessRefundArgs, ResetPasswordArgs, SearchKnowledgeResult

AsyncNode = Callable[[TicketState], Awaitable[dict[str, Any]]]
SyncNode = Callable[[TicketState], dict[str, Any]]


def checkpoint_safe_tool_result(tool_result: ToolResult) -> ToolResult:
    """Remove reset credentials from graph state before it is checkpointed."""
    if tool_result.tool_name == "reset_password" and tool_result.data is not None:
        return tool_result.model_copy(update={"data": None})
    return tool_result


def _session_tool_adapters(
    context: GraphContext,
    session: AsyncSession,
    ticket_id: uuid.UUID,
) -> ToolAdapters:
    adapters_obj = getattr(context.tool_executor, "_adapters", None)
    knowledge_adapter = (
        getattr(adapters_obj, "knowledge", None)
        if adapters_obj is not None
        else cast(Any, None)
    )
    return ToolAdapters(
        knowledge=cast(KnowledgeStore, knowledge_adapter),
        user=SqlUserRepository(session),
        order=SqlOrderRepository(session),
        service=SqlServiceRepository(session),
        credential=SqlCredentialService(session),
        refund=SqlRefundService(session, ticket_id=ticket_id),
        ticket=SqlTicketService(session, ticket_id=ticket_id),
        access_request=SqlAccessRequestService(session, ticket_id=ticket_id),
    )


async def _resource_context_for_operation(
    session: AsyncSession, pending_op: PendingOperation
) -> ResourceContext | None:
    """Build the deterministic resource facts used by both Policy and execution.

    Policy and the write gateway must evaluate the same operation against the
    same resource snapshot. Recomputing Policy in the gateway without this
    context silently downgrades self-service operations to "resource not found".
    """
    args = pending_op.validated_args
    if isinstance(args, ResetPasswordArgs):
        user = await session.get(UserRecord, args.user_id)
        return ResourceContext(
            resource_type="user",
            resource_id=args.user_id,
            owner_id=user.id if user is not None else args.user_id,
            status=(
                "not_found" if user is None else "active" if user.active else "inactive"
            ),
            version=user.version if user is not None else 1,
        )
    if isinstance(args, ProcessRefundArgs):
        order = await session.get(OrderRecord, args.order_id)
        if order is None:
            return ResourceContext(
                resource_type="order",
                resource_id=args.order_id,
                owner_id="unknown",
                status="not_found",
                version=1,
            )
        return ResourceContext(
            resource_type="order",
            resource_id=order.id,
            owner_id=order.requester_id,
            status=order.status,
            version=order.version,
            refundable_amount=order.refundable_amount,
        )
    return None


def make_supervisor_node(context: GraphContext) -> AsyncNode:
    async def supervisor_node(state: TicketState) -> dict[str, Any]:
        prompt = context.prompts["supervisor"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            think=context.settings.openai_think,
            timeout_s=context.settings.openai_timeout_s,
            max_retries=context.settings.openai_max_retries,
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(
            prompt, user_query=state["user_query"], include_user_query=False
        )
        try:
            output = await run_supervisor(
                state=state,
                system_prompt=system_prompt,
                model_client=context.model_client,
                config=config,
            )
        except ModelOutputInvalid:
            output = None

        if output is not None and not isinstance(output, RoutingDecision):
            raise TypeError(
                f"Supervisor node expected RoutingDecision, got {type(output).__name__}"
            )

        updates = {
            "intent": output.intent if output is not None else None,
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
            think=context.settings.openai_think,
            timeout_s=context.settings.openai_timeout_s,
            max_retries=context.settings.openai_max_retries,
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(
            prompt, user_query=state["user_query"], include_user_query=False
        )

        available_refs: list[KnowledgeRef] = []
        if context.tool_executor is not None:
            tool_result = await context.tool_executor.execute(
                agent_id="knowledge",
                tool_name="search_knowledge",
                raw_args={"query": state["user_query"], "top_k": 3},
            )
            if tool_result.outcome is ToolOutcome.SUCCEEDED and tool_result.data is not None:
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
            think=context.settings.openai_think,
            timeout_s=context.settings.openai_timeout_s,
            max_retries=context.settings.openai_max_retries,
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(
            prompt, user_query=state["user_query"], include_user_query=False
        )
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
        prompt = context.prompts["action"]
        config = ModelCallConfig(
            model=context.settings.openai_model or "test-model",
            think=context.settings.openai_think,
            timeout_s=context.settings.openai_timeout_s,
            max_retries=context.settings.openai_max_retries,
            prompt_version=prompt.version,
        )
        system_prompt = build_system_message(
            prompt, user_query=state["user_query"], include_user_query=False
        )
        try:
            proposal = await select_action(
                state=state,
                system_prompt=system_prompt,
                model_client=context.model_client,
                config=config,
            )
        except ActionProposalValidationError:
            # The request was deterministically refused (cross-user target or an
            # unauthorized tool directive). Returning no proposal lets the
            # Dispatcher route to the explicit rejected terminal; escalating an
            # intentional refusal would misreport a business denial as an
            # infrastructure failure.
            updates: dict[str, Any] = {
                "action_proposal": None,
                "pending_operation": None,
            }
            validate_node_output_keys("action_propose", updates)
            return updates
        if not isinstance(proposal, ActionProposal):
            raise TypeError(f"Action node expected ActionProposal, got {type(proposal).__name__}")

        if proposal.tool_name in WRITE_TOOL_NAMES:
            pending_op: PendingOperation | None = create_pending_operation(proposal)
            action_proposal: ActionProposal | None = None
        else:
            pending_op = None
            action_proposal = proposal

        updates = {
            "action_proposal": action_proposal,
            "pending_operation": pending_op,
        }
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
        resource: ResourceContext | None = None
        if isinstance(pending_op.validated_args, (ResetPasswordArgs, ProcessRefundArgs)):
            if context.session_factory is None:
                raise RuntimeError(
                    "session_factory is required to verify the operation resource"
                )
            async with context.session_factory() as session:
                resource = await _resource_context_for_operation(session, pending_op)
        decision = assess_risk(pending_op, actor=actor, resource=resource)
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
                    risk_str = risk_dec.risk_level.value if risk_dec is not None else "high"
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
        action_proposal = state.get("action_proposal")
        if pending_op is None and action_proposal is None:
            raise RuntimeError("Action execute node called without a pending operation or proposal")

        ticket_id = uuid.UUID(state["ticket_id"])
        trace_id = uuid.UUID(state["trace_id"])
        approval = state.get("approval")

        if pending_op is not None:
            if context.session_factory is None:
                raise RuntimeError("session_factory is required for write action execution")
            async with context.session_factory() as session:
                adapters = _session_tool_adapters(context, session, ticket_id)
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
                resource = await _resource_context_for_operation(session, pending_op)
                tool_result = await executor.execute(
                    agent_id="action",
                    operation=pending_op,
                    actor=actor,
                    resource=resource,
                    approval=(
                        approval
                        if approval is not None
                        and approval.operation_id == pending_op.operation_id
                        and approval.args_hash == pending_op.args_hash
                        else None
                    ),
                )
                await session.commit()
        elif context.session_factory is not None:
            assert action_proposal is not None
            async with context.session_factory() as session:
                read_executor = ReadToolExecutor(
                    adapters=_session_tool_adapters(context, session, ticket_id),
                    audit=SqlAuditSink(AuditRepository(session)),
                    ticket_id=ticket_id,
                    trace_id=trace_id,
                )
                tool_result = await read_executor.execute(
                    agent_id="action",
                    tool_name=action_proposal.tool_name,
                    raw_args=action_proposal.arguments.model_dump(mode="json"),
                )
                await session.commit()
        else:
            assert action_proposal is not None
            if context.tool_executor is None:
                raise RuntimeError("read action execution requires a ToolExecutor")
            read_executor = cast(ReadToolExecutor, context.tool_executor)
            tool_result = await read_executor.execute(
                agent_id="action",
                tool_name=action_proposal.tool_name,
                raw_args=action_proposal.arguments.model_dump(mode="json"),
            )

        results = list(state.get("tool_results", []))
        results.append(checkpoint_safe_tool_result(tool_result))
        updates = {"tool_results": results}
        validate_node_output_keys("action_execute", updates)
        return updates

    return action_execute_node


def make_dispatch_action_execute_node() -> SyncNode:
    def dispatch_action_execute_node(state: TicketState) -> dict[str, Any]:
        tool_results = state.get("tool_results", [])
        last_result = tool_results[-1] if tool_results else None
        if last_result is not None and last_result.outcome is ToolOutcome.REJECTED:
            # Keep the ticket non-terminal so the conditional edge can route to
            # the explicit rejected terminal with the correct dispatcher actor.
            next_status = None
        elif last_result is not None and last_result.outcome in (
            ToolOutcome.SUCCEEDED,
            ToolOutcome.CACHE_HIT,
        ):
            next_status = None
        else:
            next_status = TicketStatus.ESCALATED

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
