"""Domain read Tool wrappers delegate to injected Protocols only."""

from __future__ import annotations

import pytest

from backend.integrations.tool_adapters import SqlServiceRepository
from tests.support.fakes.tools import (
    RecordingKnowledgeStore,
    RecordingOrderRepository,
    RecordingServiceRepository,
    RecordingUserRepository,
    knowledge_result,
    order_result,
    service_result,
    user_result,
)
from tools.account import query_user
from tools.schemas import CheckServiceArgs, QueryOrderArgs, QueryUserArgs, SearchKnowledgeArgs
from tools.service import check_service, search_knowledge
from tools.ticket import query_order


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("vpn", "vpn"),
        ("company_vpn", "vpn"),
        ("邮件服务", "mail"),
        ("邮件系统", "mail"),
        ("视频会议", "video_conference"),
        ("核心交换机", "switch"),
        ("统一身份认证", "ldap"),
        ("内网 DNS", "dns"),
        ("访客 Wi-Fi", "guest_wifi"),
        ("认证服务", "authentication_service"),
        ("system_metrics", "system_metrics"),
        ("未知设备", "未知设备"),
    ],
)
def test_service_display_names_resolve_to_canonical_keys(
    requested: str, expected: str
) -> None:
    assert SqlServiceRepository.resolve_service_name(requested) == expected


async def test_query_user_delegates_to_the_repository() -> None:
    repository = RecordingUserRepository(result=user_result())
    args = QueryUserArgs(user_id="user-requester-001")

    result = await query_user(args, repository)

    assert result == user_result()
    assert repository.calls == [args]


async def test_query_order_delegates_to_the_repository() -> None:
    repository = RecordingOrderRepository(result=order_result())
    args = QueryOrderArgs(order_id="ORD-1001")

    result = await query_order(args, repository)

    assert result == order_result()
    assert repository.calls == [args]


async def test_check_service_delegates_to_the_repository() -> None:
    expected = service_result()
    repository = RecordingServiceRepository(result=expected)
    args = CheckServiceArgs(service_name="vpn")

    result = await check_service(args, repository)

    assert result == expected
    assert repository.calls == [args]


async def test_search_knowledge_delegates_to_the_store() -> None:
    store = RecordingKnowledgeStore(result=knowledge_result())
    args = SearchKnowledgeArgs(query="vpn", top_k=3)

    result = await search_knowledge(args, store)

    assert result.kind == "search_knowledge"
    assert len(result.refs) == 1
    assert store.calls == [args]
