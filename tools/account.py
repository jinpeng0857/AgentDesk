"""Account read capability.

Domain layer: this module depends only on ``tools.context`` Protocols and must
never import ``backend``, a database driver, Redis or ARQ.
"""

from __future__ import annotations

from tools.context import CredentialService, UserRepository
from tools.schemas import (
    QueryUserArgs,
    QueryUserResult,
    ResetPasswordArgs,
    ResetPasswordResult,
)


async def query_user(args: QueryUserArgs, repository: UserRepository) -> QueryUserResult:
    return await repository.query_user(args)


async def reset_password(
    args: ResetPasswordArgs, service: CredentialService
) -> ResetPasswordResult:
    return await service.reset_password(args)

