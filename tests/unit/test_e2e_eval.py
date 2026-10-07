"""Tests for path reconstruction and live-evaluation safety boundaries."""

from __future__ import annotations

import pytest

from eval.e2e import _psycopg_url, derive_actual_handoffs, require_isolated_test_database


def test_psycopg_checkpoint_url_preserves_password() -> None:
    dsn = _psycopg_url(
        "postgresql+asyncpg://agentdesk:secret-pass@postgres:5432/agentdesk_eval_test"
    )

    assert dsn == (
        "postgresql://agentdesk:secret-pass@postgres:5432/agentdesk_eval_test"
    )


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
