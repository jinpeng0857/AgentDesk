"""Password reset deterministic skill.

Invariants (AGENTS.md H-16):
- Only skill allowed in AgentDesk.
- Sequence of steps and on_failure branches are defined in Python and immutable.
- LLM can only invoke the skill, never alter or skip internal steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agents.action import create_pending_operation
from agents.schemas import (
    ActionProposal,
    ActorContext,
    ResourceContext,
    ToolOutcome,
    ToolResult,
)
from skills.password_reset.validate_user import (
    UserValidationError,
    validate_user_for_reset,
)
from tools.context import UserRepository
from tools.schemas import QueryUserArgs, ResetPasswordArgs, ResetPasswordResult

TEMPLATE_PATH = Path(__file__).resolve().parent / "notification_template.md"


class WriteGateway(Protocol):
    """Protocol for unified write gateway execution without backend dependency."""

    async def execute(
        self,
        *,
        agent_id: str,
        operation: Any,
        actor: ActorContext,
        resource: ResourceContext | None = None,
        approval: Any = None,
    ) -> ToolResult: ...


class PasswordResetSkillResult(BaseModel):
    """Result of executing the deterministic password reset skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    user_id: str
    temporary_password: SecretStr | None = None
    notification: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    step_history: list[str] = Field(default_factory=list)


class PasswordResetSkill:
    """Deterministic, auditable password reset skill."""

    name: str = "password_reset"

    def __init__(self, template_path: Path | None = None) -> None:
        self._template_path = template_path or TEMPLATE_PATH

    async def run(
        self,
        *,
        user_id: str,
        actor: ActorContext,
        user_repo: UserRepository,
        write_gateway: WriteGateway,
        operation_id: str | None = None,
        account_version: int | None = None,
    ) -> PasswordResetSkillResult:
        steps: list[str] = []

        # Step 1: Query user account state
        steps.append("query_user")
        try:
            user = await user_repo.query_user(QueryUserArgs(user_id=user_id))
        except Exception as exc:
            return PasswordResetSkillResult(
                success=False,
                user_id=user_id,
                error_code="USER_LOOKUP_FAILED",
                error_message=str(exc),
                step_history=steps,
            )

        # Step 2: Validate preconditions (ownership, active status)
        steps.append("validate_user")
        try:
            validate_user_for_reset(user, actor)
        except UserValidationError as exc:
            return PasswordResetSkillResult(
                success=False,
                user_id=user_id,
                error_code="VALIDATION_FAILED",
                error_message=str(exc),
                step_history=steps,
            )

        # Step 3: Formulate pending write operation
        steps.append("formulate_operation")
        version_to_use = account_version if account_version is not None else user.version
        proposal = ActionProposal(
            tool_name="reset_password",
            arguments=ResetPasswordArgs(
                user_id=user.user_id,
                account_version=version_to_use,
            ),
            evidence_result_ids=[],
        )
        pending = create_pending_operation(proposal, operation_id=operation_id)
        resource = ResourceContext(
            resource_type="user",
            resource_id=user.user_id,
            owner_id=user.user_id,
            status=user.account_status.value,
            version=version_to_use,
        )

        # Step 4: Execute via write gateway with policy and ledger governance
        steps.append("execute_gateway")
        result = await write_gateway.execute(
            agent_id="action",
            operation=pending,
            actor=actor,
            resource=resource,
        )

        if result.outcome not in (ToolOutcome.SUCCEEDED, ToolOutcome.CACHE_HIT):
            return PasswordResetSkillResult(
                success=False,
                user_id=user_id,
                error_code=result.error_code or "GATEWAY_EXECUTION_FAILED",
                error_message=f"Gateway refused operation with outcome {result.outcome.value}",
                step_history=steps,
            )

        if not isinstance(result.data, ResetPasswordResult):
            return PasswordResetSkillResult(
                success=False,
                user_id=user_id,
                error_code="INVALID_GATEWAY_DATA",
                error_message="Gateway returned unexpected result payload",
                step_history=steps,
            )

        temp_password = result.data.temporary_password

        # Step 5: Render secure notification template
        steps.append("render_notification")
        notification = self._render_template(
            username=user.masked_email,
            temporary_password=temp_password.get_secret_value(),
        )

        return PasswordResetSkillResult(
            success=True,
            user_id=user.user_id,
            temporary_password=temp_password,
            notification=notification,
            step_history=steps,
        )

    def _render_template(self, *, username: str, temporary_password: str) -> str:
        content: str
        if self._template_path.exists():
            content = self._template_path.read_text(encoding="utf-8")
        else:
            content = "Temporary password: {{ temporary_password }}"

        return (
            content.replace("{{ username }}", username).replace(
                "{{ temporary_password }}", temporary_password
            )
        )
