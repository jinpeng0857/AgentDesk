"""Deterministic demo seed data.

Creates the fixed requester/approver identities, demo orders and service states in
PostgreSQL, then rebuilds the local Chroma knowledge collection from
``backend/fixtures/knowledge.json``.

Every value here is invented and every document is redacted. The script refuses
to run without ``--reset-demo-data`` so it can never silently overwrite a
database someone is inspecting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.passwords import hash_password
from backend.config import AppSettings
from backend.db.models import OrderRecord, ServiceStatusRecord, UserRecord
from backend.db.session import async_session_factory, create_async_engine_from_settings
from backend.integrations.knowledge_store import ChromaKnowledgeStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_FIXTURE = REPOSITORY_ROOT / "backend" / "fixtures" / "knowledge.json"

DEMO_USERS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("user-requester-001", "requester", "requester-pass", "requester", True),
    ("user-requester-002", "requester2", "requester2-pass", "requester", True),
    ("user-approver-001", "approver", "approver-pass", "approver", True),
    ("user-inactive-001", "inactive", "inactive-pass", "requester", False),
)

DEMO_ORDERS: tuple[tuple[str, str, str, str, int], ...] = (
    ("ORD-1001", "user-requester-001", "paid", "50.00", 2),
    ("ORD-1002", "user-requester-001", "paid", "200.00", 3),
    ("ORD-2001", "user-requester-002", "paid", "75.00", 1),
)

DEMO_SERVICES: tuple[tuple[str, str, str | None], ...] = (
    ("vpn", "degraded", "INC-1001"),
    ("mail", "operational", None),
    ("intranet", "operational", None),
)


async def seed_users(session: AsyncSession) -> int:
    """Idempotent upsert. Deleting users would break existing ticket foreign keys."""
    for user_id, username, password, role, active in DEMO_USERS:
        await session.merge(
            UserRecord(
                id=user_id,
                username=username,
                password_hash=hash_password(SecretStr(password)),
                role=role,
                active=active,
                masked_email=f"{username[0]}***@example.com",
            )
        )
    await session.flush()
    return len(DEMO_USERS)


async def seed_orders(session: AsyncSession) -> int:
    for order_id, requester_id, status, amount, version in DEMO_ORDERS:
        await session.merge(
            OrderRecord(
                id=order_id,
                requester_id=requester_id,
                status=status,
                refundable_amount=Decimal(amount),
                currency="USD",
                version=version,
            )
        )
    await session.flush()
    return len(DEMO_ORDERS)


async def seed_services(session: AsyncSession) -> int:
    for service_name, status, incident_id in DEMO_SERVICES:
        await session.merge(
            ServiceStatusRecord(
                service_name=service_name,
                status=status,
                incident_id=incident_id,
            )
        )
    await session.flush()
    return len(DEMO_SERVICES)


def seed_knowledge(chroma_path: Path) -> int:
    store = ChromaKnowledgeStore(chroma_path)
    store.reset()
    payload: dict[str, Any] = json.loads(KNOWLEDGE_FIXTURE.read_text(encoding="utf-8"))
    added = 0
    for document in payload["documents"]:
        chunks = [
            (chunk["chunk_id"], chunk["content"]) for chunk in document["chunks"]
        ]
        added += store.add_documents(
            document_id=document["document_id"],
            title=document["title"],
            chunks=chunks,
        )
    return added


async def run(reset_demo_data: bool) -> int:
    settings = AppSettings()
    if not reset_demo_data:
        print("refusing to run without --reset-demo-data")
        return 2

    engine = create_async_engine_from_settings(settings)
    try:
        async with async_session_factory(engine)() as session:
            users = await seed_users(session)
            orders = await seed_orders(session)
            services = await seed_services(session)
            await session.commit()
    finally:
        await engine.dispose()

    chunks = seed_knowledge(settings.chroma_path)

    print(
        f"Seeded users={users} orders={orders} services={services} "
        f"knowledge_chunks={chunks} chroma_path={settings.chroma_path}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic demo data")
    parser.add_argument("--reset-demo-data", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.reset_demo_data))


if __name__ == "__main__":
    raise SystemExit(main())
