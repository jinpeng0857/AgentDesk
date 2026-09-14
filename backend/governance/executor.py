"""Read-only tool execution gateway.

The order is fixed and intentional:

    registry lookup → input schema → agent whitelist → phase 1 read capability
    → timeout → execution → output schema → redaction → audit

Both the whitelist and the read-capability gate run *before* any adapter is
resolved, so in this phase a write tool can never reach an implementation even if
one is wired by mistake. Every attempt, including rejections, is audited.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import cast

from pydantic import BaseModel, ValidationError

from agents.policy import PHASE1_READ_TOOLS, WriteCapabilityDisabled
from agents.schemas import ToolOutcome, ToolResult
from backend.governance.audit import AuditSink, ToolAttempt, redact_mapping
from tools.context import (
    AccessRequestService,
    CredentialService,
    KnowledgeStore,
    OrderRepository,
    RefundService,
    ServiceRepository,
    TicketService,
    UserRepository,
)
from tools.registry import AGENT_TOOL_WHITELIST, TOOL_MODEL_PAIRS, TOOL_REGISTRY, ToolSpec
from tools.schemas import (
    CheckServiceArgs,
    CreateTicketArgs,
    ProcessRefundArgs,
    QueryOrderArgs,
    QueryUserArgs,
    RequestAccessArgs,
    ResetPasswordArgs,
    SearchKnowledgeArgs,
    ToolName,
    ToolResultUnion,
)

ERROR_UNKNOWN_TOOL = "UNKNOWN_TOOL"
ERROR_NOT_PERMITTED = "TOOL_NOT_PERMITTED_FOR_AGENT"
ERROR_WRITE_DISABLED = "WRITE_CAPABILITY_DISABLED"
ERROR_INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
ERROR_TIMEOUT = "TOOL_TIMEOUT"
ERROR_EXECUTION = "TOOL_EXECUTION_FAILED"
ERROR_INVALID_RESULT = "INVALID_TOOL_RESULT"


@dataclass(frozen=True)
class ToolAdapters:
    """The eight domain Protocols the gateway may dispatch to."""

    knowledge: KnowledgeStore
    user: UserRepository
    order: OrderRepository
    service: ServiceRepository
    credential: CredentialService
    refund: RefundService
    ticket: TicketService
    access_request: AccessRequestService


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


class ReadToolExecutor:
    """Single entry point for every tool attempt in the read-only phase."""

    def __init__(
        self,
        *,
        adapters: ToolAdapters,
        audit: AuditSink,
        ticket_id: uuid.UUID,
        trace_id: uuid.UUID,
        registry: Mapping[str, ToolSpec] = TOOL_REGISTRY,
    ) -> None:
        self._adapters = adapters
        self._audit = audit
        self._ticket_id = ticket_id
        self._trace_id = trace_id
        self._registry = registry

    async def execute(
        self,
        *,
        agent_id: str,
        tool_name: str,
        raw_args: Mapping[str, object],
    ) -> ToolResult:
        request_id = uuid.uuid4().hex
        started = perf_counter()

        spec = self._registry.get(tool_name)
        if spec is None:
            return await self._refuse(
                request_id, agent_id, tool_name, raw_args, ERROR_UNKNOWN_TOOL, started
            )

        if tool_name not in AGENT_TOOL_WHITELIST.get(agent_id, ()):
            return await self._refuse(
                request_id, agent_id, tool_name, raw_args, ERROR_NOT_PERMITTED, started
            )

        if tool_name not in PHASE1_READ_TOOLS:
            return await self._refuse(
                request_id, agent_id, tool_name, raw_args, ERROR_WRITE_DISABLED, started
            )

        input_model, result_model = TOOL_MODEL_PAIRS[cast(ToolName, tool_name)]
        try:
            validated = input_model.model_validate(dict(raw_args))
        except ValidationError:
            return await self._refuse(
                request_id, agent_id, tool_name, raw_args, ERROR_INVALID_ARGUMENTS, started
            )

        try:
            async with asyncio.timeout(spec.timeout_s):
                produced = await self._dispatch(tool_name, validated)
        except TimeoutError:
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                validated,
                ToolOutcome.TIMED_OUT,
                ERROR_TIMEOUT,
                started,
            )
        except WriteCapabilityDisabled:
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                validated,
                ToolOutcome.REJECTED,
                ERROR_WRITE_DISABLED,
                started,
            )
        except Exception:
            # Unknown failure: classified as final so it is not retried blindly.
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                validated,
                ToolOutcome.FAILED_FINAL,
                ERROR_EXECUTION,
                started,
            )

        try:
            data = result_model.model_validate(produced)
        except ValidationError:
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                validated,
                ToolOutcome.FAILED_FINAL,
                ERROR_INVALID_RESULT,
                started,
            )

        latency_ms = _elapsed_ms(started)
        await self._audit.record(
            ToolAttempt(
                request_id=request_id,
                ticket_id=self._ticket_id,
                trace_id=self._trace_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(validated.model_dump(mode="json")),
                result_summary=redact_mapping(data.model_dump(mode="json")),
                outcome=ToolOutcome.SUCCEEDED,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=None,
            tool_name=cast(ToolName, tool_name),
            outcome=ToolOutcome.SUCCEEDED,
            data=cast(ToolResultUnion, data),
            error_code=None,
            retry_count=0,
            latency_ms=latency_ms,
        )

    async def _refuse(
        self,
        request_id: str,
        agent_id: str,
        tool_name: str,
        raw_args: Mapping[str, object],
        error_code: str,
        started: float,
    ) -> ToolResult:
        """Reject before any adapter lookup; the raw arguments are still redacted."""
        latency_ms = _elapsed_ms(started)
        await self._audit.record(
            ToolAttempt(
                request_id=request_id,
                ticket_id=self._ticket_id,
                trace_id=self._trace_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(raw_args),
                result_summary=None,
                outcome=ToolOutcome.REJECTED,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=None,
            tool_name=cast(ToolName, tool_name),
            outcome=ToolOutcome.REJECTED,
            data=None,
            error_code=error_code,
            retry_count=0,
            latency_ms=latency_ms,
        )

    async def _reject(
        self,
        request_id: str,
        agent_id: str,
        tool_name: str,
        validated: BaseModel,
        outcome: ToolOutcome,
        error_code: str,
        started: float,
    ) -> ToolResult:
        latency_ms = _elapsed_ms(started)
        await self._audit.record(
            ToolAttempt(
                request_id=request_id,
                ticket_id=self._ticket_id,
                trace_id=self._trace_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(validated.model_dump(mode="json")),
                result_summary=None,
                outcome=outcome,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=None,
            tool_name=cast(ToolName, tool_name),
            outcome=outcome,
            data=None,
            error_code=error_code,
            retry_count=0,
            latency_ms=latency_ms,
        )

    async def _dispatch(self, tool_name: str, args: BaseModel) -> BaseModel:
        adapters = self._adapters
        match args:
            case SearchKnowledgeArgs():
                return await adapters.knowledge.search(args)
            case QueryUserArgs():
                return await adapters.user.query_user(args)
            case QueryOrderArgs():
                return await adapters.order.query_order(args)
            case CheckServiceArgs():
                return await adapters.service.check_service(args)
            case CreateTicketArgs():
                return await adapters.ticket.create_ticket(args)
            case RequestAccessArgs():
                return await adapters.access_request.request_access(args)
            case ResetPasswordArgs():
                return await adapters.credential.reset_password(args)
            case ProcessRefundArgs():
                return await adapters.refund.process_refund(args)
        raise ValueError(f"no adapter mapping for {tool_name!r}")
