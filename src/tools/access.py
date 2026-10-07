"""Access request domain capability.

Domain layer: depends only on tools.context Protocols and must never import backend.
"""

from __future__ import annotations

from tools.context import AccessRequestService
from tools.schemas import RequestAccessArgs, RequestAccessResult


async def request_access(
    args: RequestAccessArgs, service: AccessRequestService
) -> RequestAccessResult:
    return await service.request_access(args)
