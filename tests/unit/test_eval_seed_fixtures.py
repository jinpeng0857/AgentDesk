"""Evaluation fixtures restore mutated shared business data between cases."""

from __future__ import annotations

from typing import Any

import pytest

from backend.db.models import UserRecord
from scripts.maintenance.seed_demo_data import DEMO_ORDERS, DEMO_SERVICES, seed_users


class _CaptureSession:
    def __init__(self) -> None:
        self.merged: list[Any] = []

    async def merge(self, instance: Any) -> Any:
        self.merged.append(instance)
        return instance

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_seed_users_resets_mutable_version_to_initial_state() -> None:
    session = _CaptureSession()

    await seed_users(session)  # type: ignore[arg-type]

    users = [item for item in session.merged if isinstance(item, UserRecord)]
    assert users
    assert all(user.version == 1 for user in users)


def test_demo_services_cover_reviewed_live_status_targets() -> None:
    names = {service[0] for service in DEMO_SERVICES}

    assert {"vpn", "mail", "guest_wifi", "dns", "video_conference", "ldap", "switch"} <= names


def test_refund_orders_match_reviewed_e2e_ownership_and_amounts() -> None:
    orders = {order[0]: order for order in DEMO_ORDERS}

    assert float(orders["ORD-1001"][3]) >= 1_000.00
    assert orders["ORD-1002"][1] == "user-requester-002"
    assert orders["ORD-1005"][1] == "user-requester-001"
    assert float(orders["ORD-1005"][3]) >= 500.00
