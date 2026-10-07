"""Trace export integration and optional Langfuse adapter.

Adheres strictly to AGENTS.md H-19:
Passwords, temporary credentials, tokens, card numbers and secrets MUST NEVER
reach external observability platforms or traces in plaintext.
All exports are sanitized via redaction before leaving the process.
Telemetry export failures MUST NEVER interrupt graph or worker execution.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from backend.config import AppSettings
from backend.governance.audit import redact_mapping

logger = logging.getLogger(__name__)


class TraceExporter(Protocol):
    """Protocol for external trace exporters."""

    async def export_event(
        self,
        *,
        trace_id: str,
        ticket_id: str,
        actor: str,
        event_type: str,
        summary: str,
        timestamp: datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Export a single execution event."""
        ...

    async def flush(self) -> None:
        """Flush buffered events."""
        ...


class NoOpTraceExporter:
    """Default no-op exporter that performs zero network requests."""

    async def export_event(
        self,
        *,
        trace_id: str,
        ticket_id: str,
        actor: str,
        event_type: str,
        summary: str,
        timestamp: datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        pass

    async def flush(self) -> None:
        pass


class LangfuseTraceExporter:
    """Optional exporter for Langfuse observability platform.

    Uses an injected client or initializes one if configured.
    Catches all exceptions so telemetry issues never break ticket workflow.
    """

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
        client: Any = None,
    ) -> None:
        self.public_key = public_key
        self.secret_key = secret_key
        self.host = host
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]

            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
            )
            return self._client
        except Exception as exc:
            logger.warning("Failed to initialize Langfuse client: %s", exc)
            return None

    async def export_event(
        self,
        *,
        trace_id: str,
        ticket_id: str,
        actor: str,
        event_type: str,
        summary: str,
        timestamp: datetime,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            # Enforce H-19: redact metadata before exporting
            safe_meta: dict[str, object] = redact_mapping(metadata) if metadata else {}
            client = self._get_client()
            if client is None:
                return

            if hasattr(client, "event"):
                client.event(
                    name=event_type,
                    trace_id=trace_id,
                    metadata={
                        "ticket_id": ticket_id,
                        "actor": actor,
                        "summary": summary,
                        **safe_meta,
                    },
                )
        except Exception as exc:
            # Never interrupt ticket execution
            logger.warning("Trace export to Langfuse failed (suppressed): %s", exc)

    async def flush(self) -> None:
        try:
            client = self._get_client()
            if client is not None and hasattr(client, "flush"):
                client.flush()
        except Exception as exc:
            logger.warning("Langfuse flush failed (suppressed): %s", exc)


def create_trace_exporter(settings: AppSettings | None = None) -> TraceExporter:
    """Factory creating the appropriate exporter based on configuration."""
    if not settings:
        return NoOpTraceExporter()

    if (
        settings.langfuse_public_key
        and settings.langfuse_secret_key
        and settings.langfuse_public_key.strip()
    ):
        return LangfuseTraceExporter(
            public_key=settings.langfuse_public_key.strip(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host,
        )

    return NoOpTraceExporter()
