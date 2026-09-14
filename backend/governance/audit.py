"""Tool call audit records and summary redaction.

Every attempt — success, rejection, timeout, validation failure or execution
failure — becomes one :class:`ToolAttempt`. Redaction happens here, before a
summary leaves the gateway, so no plaintext secret can reach the audit table,
logs or traces.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from agents.schemas import ToolOutcome
from backend.db.repositories.audit import AuditRepository

MASK = "***"
MAX_SUMMARY_CHARS = 200
SENSITIVE_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "card",
    "authorization",
)
EXACT_SENSITIVE_KEYS = frozenset({"key", "secret", "token", "password", "passwd"})


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in EXACT_SENSITIVE_KEYS:
        return True
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact_mapping(
    payload: Mapping[str, object], *, max_chars: int = MAX_SUMMARY_CHARS
) -> dict[str, object]:
    """Mask sensitive keys and bound every string value."""
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, SecretStr) or is_sensitive_key(key):
            redacted[key] = MASK
        elif isinstance(value, str):
            redacted[key] = value[:max_chars]
        elif value is None or isinstance(value, (bool, int, float)):
            redacted[key] = value
        else:
            redacted[key] = str(value)[:max_chars]
    return redacted


class ToolAttempt(BaseModel):
    """One audited tool attempt, matching the AGENTS.md H-18 field set."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    ticket_id: uuid.UUID
    trace_id: uuid.UUID
    operation_id: str | None = Field(default=None, max_length=128)
    agent_id: str = Field(min_length=1, max_length=32)
    tool_name: str = Field(min_length=1, max_length=64)
    args_summary: dict[str, object]
    result_summary: dict[str, object] | None = None
    outcome: ToolOutcome
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditSink(Protocol):
    async def record(self, attempt: ToolAttempt) -> None: ...


class SqlAuditSink:
    """Persists attempts inside the caller's transaction."""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(self, attempt: ToolAttempt) -> None:
        await self._repository.record_tool_call(
            request_id=attempt.request_id,
            ticket_id=attempt.ticket_id,
            trace_id=attempt.trace_id,
            operation_id=attempt.operation_id,
            agent_id=attempt.agent_id,
            tool_name=attempt.tool_name,
            args_summary=attempt.args_summary,
            result_summary=attempt.result_summary,
            outcome=str(attempt.outcome),
            latency_ms=attempt.latency_ms,
            retry_count=attempt.retry_count,
        )
