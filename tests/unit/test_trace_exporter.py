"""Unit tests for TraceExporter, NoOpTraceExporter, and LangfuseTraceExporter."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from backend.config import AppSettings
from backend.integrations.trace_exporter import (
    LangfuseTraceExporter,
    NoOpTraceExporter,
    create_trace_exporter,
)


@pytest.mark.asyncio
async def test_noop_trace_exporter() -> None:
    exporter = NoOpTraceExporter()
    now = datetime.now(UTC)

    # Calling export_event or flush does nothing and raises no errors
    await exporter.export_event(
        trace_id="trace-001",
        ticket_id="ticket-001",
        actor="supervisor",
        event_type="agent_step",
        summary="classify query",
        timestamp=now,
        metadata={"user": "alice", "password": "supersecretpassword"},
    )
    await exporter.flush()


def test_create_trace_exporter_defaults_to_noop() -> None:
    # No settings
    exporter1 = create_trace_exporter(None)
    assert isinstance(exporter1, NoOpTraceExporter)

    # Empty settings
    settings = AppSettings()
    exporter2 = create_trace_exporter(settings)
    assert isinstance(exporter2, NoOpTraceExporter)


def test_create_trace_exporter_with_langfuse_keys() -> None:
    settings = AppSettings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key=SecretStr("sk-lf-test"),
        langfuse_host="https://custom.langfuse.com",
    )
    exporter = create_trace_exporter(settings)
    assert isinstance(exporter, LangfuseTraceExporter)
    assert exporter.public_key == "pk-lf-test"
    assert exporter.secret_key == "sk-lf-test"
    assert exporter.host == "https://custom.langfuse.com"


@pytest.mark.asyncio
async def test_langfuse_exporter_redacts_sensitive_metadata() -> None:
    mock_client = MagicMock()
    exporter = LangfuseTraceExporter(
        public_key="pk-test",
        secret_key="sk-test",
        client=mock_client,
    )
    now = datetime.now(UTC)

    await exporter.export_event(
        trace_id="trace-101",
        ticket_id="ticket-101",
        actor="action",
        event_type="tool_call:reset_password",
        summary="Reset password executed",
        timestamp=now,
        metadata={
            "user_id": "alice",
            "password": "ClearTextPassword123!",
            "auth_token": "bearer-token-xyz",
            "api_key": "secret-key-12345",
        },
    )

    mock_client.event.assert_called_once()
    call_kwargs = mock_client.event.call_args.kwargs
    assert call_kwargs["name"] == "tool_call:reset_password"
    assert call_kwargs["trace_id"] == "trace-101"

    meta = call_kwargs["metadata"]
    assert meta["user_id"] == "alice"
    # Strict H-19 Redaction verification
    assert meta["password"] == "***"
    assert meta["auth_token"] == "***"
    assert meta["api_key"] == "***"
    assert "ClearTextPassword123!" not in str(meta)


@pytest.mark.asyncio
async def test_langfuse_exporter_suppresses_exceptions() -> None:
    mock_client = MagicMock()
    mock_client.event.side_effect = ConnectionResetError("Connection refused by Langfuse server")
    mock_client.flush.side_effect = RuntimeError("Flush buffer failure")

    exporter = LangfuseTraceExporter(
        public_key="pk-test",
        secret_key="sk-test",
        client=mock_client,
    )
    now = datetime.now(UTC)

    # Does not raise even if underlying client raises
    await exporter.export_event(
        trace_id="trace-202",
        ticket_id="ticket-202",
        actor="supervisor",
        event_type="agent_step",
        summary="routing",
        timestamp=now,
    )

    await exporter.flush()
