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
import contextlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from agents.policy import PHASE1_READ_TOOLS, WriteCapabilityDisabled, assess_risk
from agents.schemas import (
    ActorContext,
    ApprovalSnapshot,
    ApprovalStatus,
    PendingOperation,
    ResourceContext,
    ToolOutcome,
    ToolResult,
)
from backend.db.repositories.operations import OperationRepository
from backend.governance.audit import AuditSink, ToolAttempt, redact_mapping
from backend.governance.encryption import ResultEncryptor
from backend.governance.idempotency import (
    canonical_json,
    compute_args_hash,
    derive_idempotency_key,
    validate_approval_binding,
)
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
ERROR_POLICY_DENIED = "POLICY_DENIED"
ERROR_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
ERROR_APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
ERROR_CONCURRENT_EXECUTION = "CONCURRENT_EXECUTION"
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


class WriteToolExecutor:
    """Unified execution gateway for write operations with idempotency, approval, and audit.

    The execution order is fixed and audited:
        registry lookup → agent whitelist → input validation → policy & approval evaluation
        → idempotency & ledger lease → timeout / retry → adapter execution
        → result validation → redaction → audit logging
    """

    def __init__(
        self,
        *,
        adapters: ToolAdapters,
        audit: AuditSink,
        ticket_id: uuid.UUID,
        trace_id: uuid.UUID,
        operation_repo: OperationRepository | None = None,
        registry: Mapping[str, ToolSpec] = TOOL_REGISTRY,
        encryptor: ResultEncryptor | None = None,
    ) -> None:
        self._adapters = adapters
        self._audit = audit
        self._ticket_id = ticket_id
        self._trace_id = trace_id
        self._operation_repo = operation_repo
        self._registry = registry
        self._encryptor = encryptor or ResultEncryptor()

    async def execute(
        self,
        *,
        agent_id: str,
        operation: PendingOperation,
        actor: ActorContext,
        resource: ResourceContext | None = None,
        approval: ApprovalSnapshot | None = None,
    ) -> ToolResult:
        request_id = uuid.uuid4().hex
        started = perf_counter()

        tool_name = getattr(operation, "tool_name", "")
        operation_id = getattr(operation, "operation_id", None)
        raw_args = getattr(operation, "validated_args", None)
        args_hash = getattr(operation, "args_hash", None)

        spec = self._registry.get(tool_name)
        if spec is None:
            return await self._refuse(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                raw_args,
                ERROR_UNKNOWN_TOOL,
                started,
            )

        if tool_name not in AGENT_TOOL_WHITELIST.get(agent_id, ()):
            return await self._refuse(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                raw_args,
                ERROR_NOT_PERMITTED,
                started,
            )

        if tool_name not in TOOL_MODEL_PAIRS:
            return await self._refuse(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                raw_args,
                ERROR_UNKNOWN_TOOL,
                started,
            )

        input_model, result_model = TOOL_MODEL_PAIRS[cast(ToolName, tool_name)]
        if isinstance(raw_args, BaseModel):
            validated = raw_args
        else:
            try:
                validated = input_model.model_validate(dict(raw_args or {}))
            except ValidationError:
                return await self._refuse(
                    request_id,
                    agent_id,
                    tool_name,
                    operation_id,
                    raw_args,
                    ERROR_INVALID_ARGUMENTS,
                    started,
                )

        if not args_hash:
            args_hash = compute_args_hash(validated)
        idempotency_key = derive_idempotency_key(tool_name, operation_id or "", args_hash)

        # Policy & Approval check
        decision = assess_risk(operation, actor=actor, resource=resource)
        if not decision.allowed:
            return await self._refuse(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                validated,
                ERROR_POLICY_DENIED,
                started,
            )

        if decision.require_human:
            if approval is None:
                return await self._refuse(
                    request_id,
                    agent_id,
                    tool_name,
                    operation_id,
                    validated,
                    ERROR_APPROVAL_REQUIRED,
                    started,
                )
            if approval.status != ApprovalStatus.APPROVED:
                return await self._refuse(
                    request_id,
                    agent_id,
                    tool_name,
                    operation_id,
                    validated,
                    ERROR_APPROVAL_REQUIRED,
                    started,
                )
            if approval.expires_at < datetime.now(UTC):
                return await self._refuse(
                    request_id,
                    agent_id,
                    tool_name,
                    operation_id,
                    validated,
                    ERROR_APPROVAL_REQUIRED,
                    started,
                )
            try:
                validate_approval_binding(
                    approved_operation_id=approval.operation_id,
                    approved_args_hash=approval.args_hash,
                    pending_operation_id=operation_id or "",
                    pending_args_hash=args_hash,
                )
            except ValueError:
                return await self._refuse(
                    request_id,
                    agent_id,
                    tool_name,
                    operation_id,
                    validated,
                    ERROR_APPROVAL_MISMATCH,
                    started,
                )

        # Ledger check
        if self._operation_repo is not None:
            existing = await self._operation_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                if getattr(existing, "status", None) == "succeeded":
                    return await self._handle_cache_hit(
                        request_id=request_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        operation_id=operation_id,
                        validated=validated,
                        existing=existing,
                        result_model=result_model,
                        started=started,
                    )

                acquired = await self._operation_repo.acquire_lease(
                    idempotency_key, lease_holder=request_id
                )
                if not acquired:
                    return await self._refuse(
                        request_id,
                        agent_id,
                        tool_name,
                        operation_id,
                        validated,
                        ERROR_CONCURRENT_EXECUTION,
                        started,
                    )
            else:
                created = await self._operation_repo.create_operation(
                    ticket_id=self._ticket_id,
                    operation_id=operation_id or "",
                    tool_name=tool_name,
                    args_hash=args_hash,
                    idempotency_key=idempotency_key,
                    status="running",
                    lease_holder=request_id,
                )
                if created is None:
                    existing = await self._operation_repo.get_by_idempotency_key(idempotency_key)
                    if existing is not None and getattr(existing, "status", None) == "succeeded":
                        return await self._handle_cache_hit(
                            request_id=request_id,
                            agent_id=agent_id,
                            tool_name=tool_name,
                            operation_id=operation_id,
                            validated=validated,
                            existing=existing,
                            result_model=result_model,
                            started=started,
                        )
                    acquired = await self._operation_repo.acquire_lease(
                        idempotency_key, lease_holder=request_id
                    )
                    if not acquired:
                        return await self._refuse(
                            request_id,
                            agent_id,
                            tool_name,
                            operation_id,
                            validated,
                            ERROR_CONCURRENT_EXECUTION,
                            started,
                        )

        # Execution
        try:
            async with asyncio.timeout(spec.timeout_s):
                produced = await self._dispatch(tool_name, validated)
        except TimeoutError:
            if self._operation_repo is not None:
                await self._operation_repo.record_failure(
                    idempotency_key,
                    status="timed_out",
                    result_summary={"error": ERROR_TIMEOUT},
                )
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                validated,
                ToolOutcome.TIMED_OUT,
                ERROR_TIMEOUT,
                started,
            )
        except Exception as exc:
            if self._operation_repo is not None:
                await self._operation_repo.record_failure(
                    idempotency_key,
                    status="failed_final",
                    result_summary={"error": str(exc)[:200]},
                )
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                validated,
                ToolOutcome.FAILED_FINAL,
                ERROR_EXECUTION,
                started,
            )

        try:
            data = result_model.model_validate(produced)
        except ValidationError:
            if self._operation_repo is not None:
                await self._operation_repo.record_failure(
                    idempotency_key,
                    status="failed_final",
                    result_summary={"error": ERROR_INVALID_RESULT},
                )
            return await self._reject(
                request_id,
                agent_id,
                tool_name,
                operation_id,
                validated,
                ToolOutcome.FAILED_FINAL,
                ERROR_INVALID_RESULT,
                started,
            )

        result_dict = data.model_dump(mode="python")
        result_summary = redact_mapping(result_dict)
        if self._operation_repo is not None:
            encrypted_payload = self._encryptor.encrypt(canonical_json(result_dict))
            await self._operation_repo.record_success(
                idempotency_key,
                encrypted_result=encrypted_payload,
                result_summary=result_summary,
            )

        latency_ms = _elapsed_ms(started)
        await self._audit.record(
            ToolAttempt(
                request_id=request_id,
                ticket_id=self._ticket_id,
                trace_id=self._trace_id,
                operation_id=operation_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(validated.model_dump(mode="python")),
                result_summary=result_summary,
                outcome=ToolOutcome.SUCCEEDED,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=operation_id,
            tool_name=cast(ToolName, tool_name),
            outcome=ToolOutcome.SUCCEEDED,
            data=cast(ToolResultUnion, data),
            error_code=None,
            retry_count=0,
            latency_ms=latency_ms,
        )

    async def _handle_cache_hit(
        self,
        *,
        request_id: str,
        agent_id: str,
        tool_name: str,
        operation_id: str | None,
        validated: BaseModel,
        existing: Any,
        result_model: type[BaseModel],
        started: float,
    ) -> ToolResult:
        cached_data: ToolResultUnion | None = None
        enc = getattr(existing, "encrypted_result", None)
        if enc:
            with contextlib.suppress(Exception):
                decrypted = self._encryptor.decrypt(enc)
                cached_data = cast(
                    ToolResultUnion,
                    result_model.model_validate(json.loads(decrypted)),
                )
            if cached_data is None:
                with contextlib.suppress(Exception):
                    cached_data = cast(
                        ToolResultUnion,
                        result_model.model_validate(json.loads(enc)),
                    )
        if cached_data is None:
            summary_dict = getattr(existing, "result_summary", None)
            if summary_dict:
                with contextlib.suppress(Exception):
                    cached_data = cast(
                        ToolResultUnion,
                        result_model.model_validate(summary_dict),
                    )
        latency_ms = _elapsed_ms(started)
        res_summary = getattr(existing, "result_summary", None) or (
            redact_mapping(cached_data.model_dump(mode="python"))
            if cached_data
            else {}
        )
        await self._audit.record(
            ToolAttempt(
                request_id=request_id,
                ticket_id=self._ticket_id,
                trace_id=self._trace_id,
                operation_id=operation_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(validated.model_dump(mode="python")),
                result_summary=res_summary,
                outcome=ToolOutcome.CACHE_HIT,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=operation_id,
            tool_name=cast(ToolName, tool_name),
            outcome=ToolOutcome.CACHE_HIT,
            data=cached_data,
            error_code=None,
            retry_count=0,
            latency_ms=latency_ms,
        )

    async def _refuse(
        self,
        request_id: str,
        agent_id: str,
        tool_name: str,
        operation_id: str | None,
        raw_args: Mapping[str, object] | BaseModel | None,
        error_code: str,
        started: float,
    ) -> ToolResult:
        latency_ms = _elapsed_ms(started)
        if isinstance(raw_args, BaseModel):
            args_dict: dict[str, object] = raw_args.model_dump(mode="python")
        elif isinstance(raw_args, Mapping):
            args_dict = dict(raw_args)
        else:
            args_dict = {}
        await self._audit.record(
            ToolAttempt(
                request_id=request_id,
                ticket_id=self._ticket_id,
                trace_id=self._trace_id,
                operation_id=operation_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(args_dict),
                result_summary=None,
                outcome=ToolOutcome.REJECTED,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=operation_id,
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
        operation_id: str | None,
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
                operation_id=operation_id,
                agent_id=agent_id,
                tool_name=tool_name,
                args_summary=redact_mapping(validated.model_dump(mode="python")),
                result_summary=None,
                outcome=outcome,
                latency_ms=latency_ms,
            )
        )
        return ToolResult(
            request_id=request_id,
            operation_id=operation_id,
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
            case CreateTicketArgs():
                return await adapters.ticket.create_ticket(args)
            case RequestAccessArgs():
                return await adapters.access_request.request_access(args)
            case ResetPasswordArgs():
                return await adapters.credential.reset_password(args)
            case ProcessRefundArgs():
                return await adapters.refund.process_refund(args)
            case SearchKnowledgeArgs():
                return await adapters.knowledge.search(args)
            case QueryUserArgs():
                return await adapters.user.query_user(args)
            case QueryOrderArgs():
                return await adapters.order.query_order(args)
            case CheckServiceArgs():
                return await adapters.service.check_service(args)
        raise ValueError(f"no adapter mapping for {tool_name!r}")

