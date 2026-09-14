"""Service and knowledge read capabilities.

Domain layer: this module depends only on ``tools.context`` Protocols and must
never import ``backend``, a database driver, Redis or ARQ.
"""

from __future__ import annotations

from tools.context import KnowledgeStore, ServiceRepository
from tools.schemas import (
    CheckServiceArgs,
    CheckServiceResult,
    SearchKnowledgeArgs,
    SearchKnowledgeResult,
)


async def check_service(
    args: CheckServiceArgs, repository: ServiceRepository
) -> CheckServiceResult:
    return await repository.check_service(args)


async def search_knowledge(
    args: SearchKnowledgeArgs, store: KnowledgeStore
) -> SearchKnowledgeResult:
    return await store.search(args)
