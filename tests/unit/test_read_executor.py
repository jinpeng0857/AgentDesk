"""The read-only executor gates schema, whitelist, capability, timeout and audit."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

import pytest

from agents.schemas import ToolOutcome
from backend.governance.audit import ToolAttempt, redact_mapping
from backend.governance.executor import ReadToolExecutor, ToolAdapters
from tests.support.fakes.tools import (
    DisabledAccessRequestService,
    DisabledCredentialService,
    DisabledRefundService,
    DisabledTicketService,
    InMemoryAuditSink,
    RecordingKnowledgeStore,
    RecordingOrderRepository,
    RecordingServiceRepository,
    RecordingUserRepository,
    knowledge_result,
    order_result,
    service_result,
    user_result,
)
from tools.registry import TOOL_REGISTRY
from tools.schemas import ToolName

WRITE_TOOLS = ("create_ticket", "request_access", "reset_password", "process_refund")


class Harness:
    def __init__(self, *, timeout_s: float | None = None) -> None:
        self.audit = InMemoryAuditSink()
        self.knowledge = RecordingKnowledgeStore(result=knowledge_result())
        self.users = RecordingUserRepository(result=user_result())
        self.orders = RecordingOrderRepository(result=order_result())
        self.services = RecordingServiceRepository(result=service_result())
        self.credentials = DisabledCredentialService()
        self.refunds = DisabledRefundService()
        self.tickets = DisabledTicketService()
        self.access_requests = DisabledAccessRequestService()

        registry: Mapping[ToolName, object] = TOOL_REGISTRY
        if timeout_s is not None:
            registry = {
                **TOOL_REGISTRY,
                "check_service": TOOL_REGISTRY["check_service"].model_copy(
                    update={"timeout_s": timeout_s}
                ),
            }

        self.executor = ReadToolExecutor(
            adapters=ToolAdapters(
                knowledge=self.knowledge,
                user=self.users,
                order=self.orders,
                service=self.services,
                credential=self.credentials,
                refund=self.refunds,
                ticket=self.tickets,
                access_request=self.access_requests,
            ),
            audit=self.audit,
            ticket_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            registry=registry,  # type: ignore[arg-type]
        )

    @property
    def write_adapter_calls(self) -> int:
        return (
            len(self.credentials.calls)
            + len(self.refunds.calls)
            + len(self.tickets.calls)
            + len(self.access_requests.calls)
        )


@pytest.mark.parametrize("tool_name", WRITE_TOOLS)
async def test_every_write_tool_is_rejected_before_any_adapter_runs(tool_name: str) -> None:
    harness = Harness()

    result = await harness.executor.execute(agent_id="action", tool_name=tool_name, raw_args={})

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "WRITE_CAPABILITY_DISABLED"
    assert result.data is None
    assert harness.write_adapter_calls == 0
    assert len(harness.audit.attempts) == 1
    assert harness.audit.attempts[0].tool_name == tool_name


async def test_unknown_tool_is_rejected_and_audited() -> None:
    harness = Harness()

    result = await harness.executor.execute(
        agent_id="action", tool_name="delete_user", raw_args={}
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "UNKNOWN_TOOL"
    assert len(harness.audit.attempts) == 1


async def test_agent_whitelist_is_enforced_before_dispatch() -> None:
    harness = Harness()

    result = await harness.executor.execute(
        agent_id="supervisor", tool_name="search_knowledge", raw_args={"query": "vpn"}
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "TOOL_NOT_PERMITTED_FOR_AGENT"
    assert harness.knowledge.calls == []
    assert len(harness.audit.attempts) == 1


async def test_invalid_arguments_are_rejected_without_dispatch() -> None:
    harness = Harness()

    result = await harness.executor.execute(
        agent_id="knowledge", tool_name="search_knowledge", raw_args={}
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "INVALID_ARGUMENTS"
    assert harness.knowledge.calls == []
    assert len(harness.audit.attempts) == 1


async def test_unknown_arguments_are_rejected_by_the_schema() -> None:
    harness = Harness()

    result = await harness.executor.execute(
        agent_id="knowledge",
        tool_name="search_knowledge",
        raw_args={"query": "vpn", "smuggled": "value"},
    )

    assert result.outcome is ToolOutcome.REJECTED
    assert result.error_code == "INVALID_ARGUMENTS"


async def test_successful_read_returns_validated_data_and_audits_it() -> None:
    harness = Harness()

    result = await harness.executor.execute(
        agent_id="knowledge", tool_name="search_knowledge", raw_args={"query": "vpn"}
    )

    assert result.outcome is ToolOutcome.SUCCEEDED
    assert result.error_code is None
    assert result.data is not None
    assert result.data.kind == "search_knowledge"
    assert len(harness.knowledge.calls) == 1
    assert harness.knowledge.calls[0].query == "vpn"

    attempt = harness.audit.attempts[0]
    assert attempt.outcome is ToolOutcome.SUCCEEDED
    assert attempt.args_summary["query"] == "vpn"
    assert attempt.retry_count == 0
    assert attempt.latency_ms >= 0
    assert attempt.agent_id == "knowledge"


async def test_timeout_is_recorded_as_timed_out() -> None:
    harness = Harness(timeout_s=0.01)
    harness.services.delay_s = 0.5

    result = await harness.executor.execute(
        agent_id="action", tool_name="check_service", raw_args={"service_name": "vpn"}
    )

    assert result.outcome is ToolOutcome.TIMED_OUT
    assert result.error_code == "TOOL_TIMEOUT"
    assert harness.audit.attempts[0].outcome is ToolOutcome.TIMED_OUT


async def test_adapter_failure_is_recorded_as_final_failure() -> None:
    harness = Harness()
    harness.services.error = RuntimeError("database is unavailable")

    result = await harness.executor.execute(
        agent_id="action", tool_name="check_service", raw_args={"service_name": "vpn"}
    )

    assert result.outcome is ToolOutcome.FAILED_FINAL
    assert result.error_code == "TOOL_EXECUTION_FAILED"
    assert "database is unavailable" not in (result.error_code or "")
    assert len(harness.audit.attempts) == 1


def test_redaction_masks_sensitive_keys_and_truncates_long_values() -> None:
    redacted = redact_mapping(
        {
            "user_id": "user-requester-001",
            "temporary_password": "sup3r-s3cret",
            "api_key": "sk-live-abcdef",
            "description": "x" * 500,
        }
    )

    assert redacted["user_id"] == "user-requester-001"
    assert redacted["temporary_password"] == "***"
    assert redacted["api_key"] == "***"
    assert isinstance(redacted["description"], str)
    assert len(redacted["description"]) < 500


def test_tool_attempt_requires_the_h_18_audit_fields() -> None:
    fields = set(ToolAttempt.model_fields)

    assert {
        "request_id",
        "ticket_id",
        "trace_id",
        "operation_id",
        "agent_id",
        "tool_name",
        "args_summary",
        "result_summary",
        "outcome",
        "latency_ms",
        "retry_count",
        "timestamp",
    } <= fields
