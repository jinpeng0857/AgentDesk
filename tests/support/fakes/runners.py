"""Deterministic test doubles for worker-side collaborators."""

from __future__ import annotations

from agents.schemas import TicketStatus


class RecordingTicketRunner:
    """Records invocations instead of running a graph."""

    def __init__(self, result: TicketStatus = TicketStatus.RESOLVED) -> None:
        self.result = result
        self.calls: list[str] = []

    async def run(self, ticket_id: str) -> TicketStatus:
        self.calls.append(ticket_id)
        return self.result

    async def fail(self, ticket_id: str, failure_code: str) -> TicketStatus:
        self.calls.append(f"fail:{ticket_id}:{failure_code}")
        return TicketStatus.ESCALATED


class UnavailableTicketRunner:
    """Fails every run, standing in for an unimplemented runtime."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, ticket_id: str) -> TicketStatus:
        self.calls.append(ticket_id)
        raise RuntimeError(f"ticket runner is not available for {ticket_id}")

    async def fail(self, ticket_id: str, failure_code: str) -> TicketStatus:
        self.calls.append(f"fail:{ticket_id}:{failure_code}")
        raise RuntimeError(f"ticket failure handler is not available for {ticket_id}")


class FailingRedis:
    """Minimal ARQ pool stand-in whose enqueue always fails."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def enqueue_job(
        self, function: str, *args: object, **kwargs: object
    ) -> None:
        self.calls.append((function, args, kwargs))
        raise self._error
