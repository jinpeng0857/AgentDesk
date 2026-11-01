"""Graph-level regression tests for deterministic rejection and knowledge evidence.

These run without PostgreSQL or a live model: the graph is built with a queued
FakeModelClient and a stub read executor. They verify the wiring that the live
80-case E2E exercised, not provider behaviour.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from agents.schemas import (
    ActionProposal,
    FinalResponse,
    Intent,
    KnowledgeAgentOutput,
    KnowledgeRef,
    ResolutionMode,
    RoutingDecision,
    RoutingReason,
    TicketStatus,
    ToolOutcome,
    ToolResult,
)
from backend.config import AppSettings
from backend.db.models import TicketRecord
from backend.graph.builder import build_ticket_graph
from backend.graph.context import GraphContext
from backend.graph.nodes import (
    make_action_propose_node,
    make_knowledge_node,
    make_supervisor_node,
)
from backend.graph.state import initial_ticket_state
from backend.integrations.prompt_loader import load_all_prompts
from tests.support.fakes.model_client import FakeModelClient
from tools.schemas import (
    CheckServiceArgs,
    KnowledgeSearchItem,
    ResetPasswordArgs,
    SearchKnowledgeResult,
)


class _StubReadExecutor:
    def __init__(self, result: ToolResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def execute(
        self, *, agent_id: str, tool_name: str, raw_args: Mapping[str, object]
    ) -> ToolResult:
        self.calls.append((agent_id, tool_name))
        return self._result


def _ticket(query: str) -> TicketRecord:
    return TicketRecord(
        id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        thread_id="b786ff16-f8b7-420a-9ccf-b83f76755978",
        requester_id="user-requester-001",
        user_query=query,
        trace_id="efed6bb0-2f9c-4454-a9f4-511992dc1c86",
        status="queued",
        step_count=0,
    )


def _context(
    fake: FakeModelClient, executor: _StubReadExecutor | None = None
) -> GraphContext:
    settings = AppSettings(
        app_env="test",
        openai_model="test-model",
        openai_base_url="http://127.0.0.1:1/v1",
    )
    return GraphContext(
        model_client=fake,
        prompts=load_all_prompts(),
        settings=settings,
        tool_executor=executor,
        session_factory=None,
    )


async def _run(
    fake: FakeModelClient, query: str, executor: _StubReadExecutor | None = None
) -> tuple[dict[str, Any], list[str]]:
    checkpointer = InMemorySaver()
    graph = build_ticket_graph(_context(fake, executor), checkpointer=checkpointer)
    state = initial_ticket_state(_ticket(query))
    config = {"configurable": {"thread_id": state["thread_id"]}}
    updates: list[str] = []
    async for chunk in graph.astream(state, config=config, stream_mode="updates"):
        for node in chunk:
            updates.append(node)
    snapshot = await graph.aget_state(config)
    values = snapshot.values
    if not isinstance(values, Mapping):
        raise AssertionError("graph produced no checkpoint state")
    return dict(values), updates


async def test_supervisor_receives_the_user_request_once_in_its_user_message() -> None:
    query = "账号异地频繁告警，请帮我查询安全记录并生成安全排查工单。"
    fake = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.ACCOUNT,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            )
        ]
    )
    node = make_supervisor_node(_context(fake))

    await node(initial_ticket_state(_ticket(query)))

    messages = fake.calls[0].messages
    assert query not in messages[0].content
    assert query in messages[1].content


async def test_action_receives_the_user_request_once_in_its_user_message() -> None:
    query = "请查询 VPN 状态"
    fake = FakeModelClient(
        [
            ActionProposal(
                tool_name="check_service",
                arguments=CheckServiceArgs(service_name="vpn"),
                evidence_result_ids=[],
            )
        ]
    )
    node = make_action_propose_node(_context(fake))
    state = initial_ticket_state(_ticket(query))
    state["intent"] = Intent.NETWORK

    await node(state)

    messages = fake.calls[0].messages
    assert query not in messages[0].content
    assert query in messages[1].content


async def test_knowledge_receives_the_user_request_once_in_its_user_message() -> None:
    query = "公司 VPN 无法连接，请给出恢复 SOP。"
    search_result = ToolResult(
        request_id="request-knowledge-context",
        operation_id=None,
        tool_name="search_knowledge",
        outcome=ToolOutcome.SUCCEEDED,
        data=SearchKnowledgeResult(
            refs=[
                KnowledgeSearchItem(
                    document_id="doc-network-vpn",
                    chunk_id="doc-network-vpn#1",
                    title="VPN 连接故障排查 SOP",
                    content_excerpt="确认 VPN 客户端和网络连接。",
                    score=0.8,
                )
            ]
        ),
        error_code=None,
        retry_count=0,
        latency_ms=1,
    )
    fake = FakeModelClient(
        [KnowledgeAgentOutput(answer="请检查 VPN 客户端。", knowledge_refs=[])]
    )
    node = make_knowledge_node(_context(fake, _StubReadExecutor(search_result)))

    await node(initial_ticket_state(_ticket(query)))

    messages = fake.calls[0].messages
    assert query not in messages[0].content
    assert query in messages[1].content


async def test_cross_user_reset_is_rejected_without_executing_a_write() -> None:
    proposal = ActionProposal(
        tool_name="reset_password",
        arguments=ResetPasswordArgs(user_id="user-requester-001", account_version=1),
        evidence_result_ids=[],
    )
    fake = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.ACCOUNT,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            proposal,
            proposal,
        ]
    )

    state, updates = await _run(fake, "请为 user-requester-002 重置密码。")

    assert updates[-1] == "reject_request"
    assert state["status"] is TicketStatus.REJECTED
    assert state["tool_results"] == []


async def test_governance_rejected_tool_ends_in_rejected_terminal() -> None:
    rejected = ToolResult(
        request_id="request-2",
        operation_id=None,
        tool_name="search_knowledge",
        outcome=ToolOutcome.REJECTED,
        data=None,
        error_code="TOOL_NOT_PERMITTED_FOR_AGENT",
        retry_count=0,
        latency_ms=1,
    )
    fake = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="action",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_ACTION,
            ),
            ActionProposal(
                tool_name="check_service",
                arguments={"kind": "check_service", "service_name": "vpn"},
                evidence_result_ids=[],
            ),
        ]
    )

    state, updates = await _run(fake, "查询 VPN 状态", _StubReadExecutor(rejected))

    assert updates[-1] == "reject_request"
    assert state["status"] is TicketStatus.REJECTED


async def test_knowledge_evidence_survives_a_model_that_invents_every_citation() -> None:
    real_ref = KnowledgeRef(
        document_id="doc-network-vpn",
        chunk_id="doc-network-vpn#1",
        title="VPN 连接故障排查 SOP",
        content_excerpt="确认公司 VPN 客户端已升级到受支持版本。",
        score=0.72,
    )
    search_result = ToolResult(
        request_id="request-1",
        operation_id=None,
        tool_name="search_knowledge",
        outcome=ToolOutcome.SUCCEEDED,
        data=SearchKnowledgeResult(
            refs=[
                KnowledgeSearchItem(
                    document_id=real_ref.document_id,
                    chunk_id=real_ref.chunk_id,
                    title=real_ref.title,
                    content_excerpt=real_ref.content_excerpt,
                    score=real_ref.score,
                )
            ]
        ),
        error_code=None,
        retry_count=0,
        latency_ms=1,
    )
    invented = KnowledgeRef(
        document_id="DOC-FAKE-999",
        chunk_id="chunk-999",
        title="Invented",
        content_excerpt="Never retrieved.",
        score=0.99,
    )
    fake = FakeModelClient(
        [
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            KnowledgeAgentOutput(answer="请升级客户端。", knowledge_refs=[invented]),
            RoutingDecision(
                intent=Intent.NETWORK,
                next_agent="knowledge",
                confidence=0.95,
                escalate=False,
                reason_code=RoutingReason.NEEDS_KNOWLEDGE,
            ),
            FinalResponse(
                resolution_mode=ResolutionMode.AUTOMATED,
                final_response="请升级 VPN 客户端后重试。",
            ),
        ]
    )

    state, updates = await _run(
        fake, "公司 VPN 无法连接，请给出恢复 SOP。", _StubReadExecutor(search_result)
    )

    assert state["knowledge_refs"] and state["knowledge_refs"][0].chunk_id == "doc-network-vpn#1"
    assert updates[-1] == "dispatch_finalizer"
    assert state["status"] is TicketStatus.RESOLVED
