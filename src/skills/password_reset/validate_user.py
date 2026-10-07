"""Precondition validation for password reset skill."""

from __future__ import annotations

from agents.schemas import ActorContext
from tools.schemas import AccountStatus, QueryUserResult


class UserValidationError(ValueError):
    """Raised when user fails password reset preconditions."""


def validate_user_for_reset(
    user: QueryUserResult,
    actor: ActorContext,
) -> None:
    """Verify that user exists, is active, and is owned by actor.

    Invariants (AGENTS.md H-16):
    - Actor must be authenticated.
    - Requester must be the account owner (actor.user_id == user.user_id).
    - Account status must be ACTIVE.
    """
    if not actor.authenticated:
        raise UserValidationError(f"Actor {actor.user_id} is not authenticated")

    if user.user_id != actor.user_id:
        raise UserValidationError(
            f"Actor {actor.user_id} cannot reset password for {user.user_id}: not owner"
        )

    if user.account_status != AccountStatus.ACTIVE:
        raise UserValidationError(
            f"User {user.user_id} is inactive; password reset is prohibited"
        )
