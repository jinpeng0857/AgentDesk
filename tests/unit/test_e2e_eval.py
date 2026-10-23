"""Tests for path reconstruction and live-evaluation safety boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.integrations.model_client import ModelOutputInvalid
from eval.e2e import (
    LiveEvalConfigurationError,
    _CaseReadWriteLock,
    _format_e2e_error,
    _psycopg_url,
    derive_actual_handoffs,
    require_isolated_test_database,
    validate_e2e_dataset,
)
from eval.run_eval import load_dataset


def test_psycopg_checkpoint_url_preserves_password() -> None:
    dsn = _psycopg_url(
        "postgresql+asyncpg://agentdesk:secret-pass@postgres:5432/agentdesk_eval_test"
    )
    assert dsn == (
        "postgresql://agentdesk:secret-pass@postgres:5432/agentdesk_eval_test"
    )


def test_e2e_error_summary_has_safe_schema_diagnostics_and_last_node() -> None:
    secret = "provider-response-secret"
    error = ModelOutputInvalid(
        f"bad response {secret}",
        output_model="KnowledgeAgentOutput",
        validation_summary="answer: missing; <root>: json_invalid",
        repair_attempts=2,
    )

    summary = _format_e2e_error(error, ["knowledge"])

    assert summary == (
        "ModelOutputInvalid:KnowledgeAgentOutput:repairs=2:"
        "validation=answer: missing; <root>: json_invalid:after=knowledge"
    )
    assert secret not in summary


def test_read_tool_executor_is_not_reported_as_a_role_handoff() -> None:
    nodes = [
        "supervisor",
        "dispatch_supervisor",
        "action",
        "dispatch_action",
        "action_execute",
        "dispatch_action_execute",
        "supervisor",
        "dispatch_supervisor",
        "finalizer",
    ]

    handoffs = derive_actual_handoffs(nodes)

    assert [(item.source, item.target) for item in handoffs] == [
        ("supervisor", "action_propose"),
        ("action_propose", "supervisor"),
        ("supervisor", "finalize"),
    ]


def test_write_tool_handoffs_preserve_policy_and_execution_nodes() -> None:
    nodes = [
        "supervisor",
        "dispatch_supervisor",
        "action",
        "dispatch_action",
        "policy",
        "dispatch_policy",
        "action_execute",
        "dispatch_action_execute",
        "supervisor",
        "dispatch_supervisor",
        "finalizer",
    ]

    handoffs = derive_actual_handoffs(nodes)

    assert [(item.source, item.target) for item in handoffs] == [
        ("supervisor", "action_propose"),
        ("action_propose", "policy"),
        ("policy", "action_execute"),
        ("action_execute", "supervisor"),
        ("supervisor", "finalize"),
    ]


def test_approval_interrupt_is_measured_without_approving_it() -> None:
    nodes = [
        "supervisor",
        "action",
        "policy",
        "approval",
    ]

    handoffs = derive_actual_handoffs(nodes)

    assert [(item.source, item.target) for item in handoffs] == [
        ("supervisor", "action_propose"),
        ("action_propose", "policy"),
        ("policy", "approval"),
    ]


def test_langgraph_interrupt_event_counts_as_policy_to_approval_handoff() -> None:
    nodes = [
        "supervisor",
        "dispatch_supervisor",
        "action",
        "dispatch_action",
        "policy",
        "dispatch_policy",
        "__interrupt__",
    ]

    handoffs = derive_actual_handoffs(nodes)

    assert [(item.source, item.target) for item in handoffs] == [
        ("supervisor", "action_propose"),
        ("action_propose", "policy"),
        ("policy", "approval"),
    ]


def test_live_eval_database_must_be_isolated() -> None:
    assert (
        require_isolated_test_database(
            "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk_eval_test"
        )
        == "agentdesk_eval_test"
    )
    with pytest.raises(RuntimeError, match="must end in '_test'"):
        require_isolated_test_database(
            "postgresql+asyncpg://agentdesk:agentdesk@localhost:5432/agentdesk"
        )


def test_full_e2e_gate_accepts_only_reviewed_eighty_case_dataset() -> None:
    full_dataset = load_dataset(
        Path(__file__).resolve().parents[2] / "resources/evaluation/golden_dataset.json"
    )
    validate_e2e_dataset(full_dataset)

    smoke_dataset = full_dataset.model_copy(
        update={"level": "smoke", "cases": full_dataset.cases[:50]}
    )
    with pytest.raises(LiveEvalConfigurationError, match="exactly 80 cases"):
        validate_e2e_dataset(smoke_dataset)


async def test_case_gate_allows_readers_concurrently_and_excludes_writers() -> None:
    gate = _CaseReadWriteLock()
    active_readers = 0
    max_readers = 0
    readers_ready = asyncio.Event()
    release_readers = asyncio.Event()
    writer_entered = asyncio.Event()
    release_writer = asyncio.Event()
    late_reader_entered = asyncio.Event()

    async def reader(*, late: bool = False) -> None:
        nonlocal active_readers, max_readers
        async with gate.read():
            active_readers += 1
            max_readers = max(max_readers, active_readers)
            if late:
                late_reader_entered.set()
            if active_readers == 2:
                readers_ready.set()
            if not late:
                await release_readers.wait()
            active_readers -= 1

    async def writer() -> None:
        async with gate.write():
            writer_entered.set()
            await release_writer.wait()

    first_readers = [asyncio.create_task(reader()) for _ in range(2)]
    await readers_ready.wait()
    writer_task = asyncio.create_task(writer())
    async with gate._condition:
        await gate._condition.wait_for(lambda: gate._waiting_writers == 1)
    late_reader_task = asyncio.create_task(reader(late=True))
    release_readers.set()
    await writer_entered.wait()
    assert not late_reader_entered.is_set()
    release_writer.set()
    await asyncio.gather(*first_readers, writer_task, late_reader_task)

    assert max_readers == 2
    assert late_reader_entered.is_set()
