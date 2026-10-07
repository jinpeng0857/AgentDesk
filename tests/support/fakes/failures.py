"""Deterministic failure injection fakes for failure modes testing.

Allows simulating exact 'fails on the first N calls' without probabilistic faults
or real sleep delays.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tools.schemas import (
    ProcessRefundArgs,
    ProcessRefundResult,
    SearchKnowledgeArgs,
    SearchKnowledgeResult,
)


@dataclass
class FailingKnowledgeStore:
    """Simulates a knowledge store that fails the first `fail_count` times."""

    result: SearchKnowledgeResult
    fail_count: int = 3
    call_count: int = 0
    calls: list[SearchKnowledgeArgs] = field(default_factory=list)
    error_factory: Callable[[], Exception] = field(
        default_factory=lambda: lambda: TimeoutError("Simulated knowledge store timeout")
    )

    async def search(self, args: SearchKnowledgeArgs) -> SearchKnowledgeResult:
        self.calls.append(args)
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise self.error_factory()
        return self.result


@dataclass
class FailingRefundService:
    """Simulates a refund service that fails the first `fail_count` times."""

    result: ProcessRefundResult
    fail_count: int = 3
    call_count: int = 0
    side_effect_count: int = 0
    calls: list[ProcessRefundArgs] = field(default_factory=list)
    error_factory: Callable[[], Exception] = field(
        default_factory=lambda: lambda: TimeoutError("Simulated refund gateway timeout")
    )

    async def process_refund(self, args: ProcessRefundArgs) -> ProcessRefundResult:
        self.calls.append(args)
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise self.error_factory()
        self.side_effect_count += 1
        return self.result
