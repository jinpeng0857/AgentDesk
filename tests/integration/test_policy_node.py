from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.action import create_pending_operation
from agents.schemas import ActionProposal, Intent, PolicyReason, TicketStatus
from backend.config import AppSettings
from backend.db.models import TicketRecord
from backend.graph.context import GraphContext
from backend.graph.nodes import make_policy_node
from backend.graph.state import initial_ticket_state
from tests.support.fakes.model_client import FakeModelClient
from tools.schemas import ResetPasswordArgs


async def test_policy_node_loads_account_state_before_approving_password_reset(
    session_factory: async_sessionmaker[AsyncSession], seeded_demo_data: None
) -> None:
    requester_id = "user-inactive-001"
    record = TicketRecord(
        id=uuid.uuid4(),
        thread_id=str(uuid.uuid4()),
        requester_id=requester_id,
        user_query="请为我本人状态为 inactive 的账号重置密码。",
        trace_id=uuid.uuid4(),
        status=TicketStatus.RUNNING.value,
        intent=Intent.ACCOUNT.value,
        step_count=0,
    )
    state = initial_ticket_state(record)
    proposal = ActionProposal(
        tool_name="reset_password",
        arguments=ResetPasswordArgs(user_id=requester_id, account_version=1),
        evidence_result_ids=[],
    )
    state["pending_operation"] = create_pending_operation(proposal)
    context = GraphContext(
        model_client=FakeModelClient([]),
        prompts={},
        settings=AppSettings(app_env="test"),
        session_factory=session_factory,
    )

    updates = await make_policy_node(context)(state)

    decision = updates["risk_decision"]
    assert decision is not None
    assert decision.allowed is False
    assert decision.reason_code is PolicyReason.DENY_INACTIVE_ACCOUNT
