"""Empty-database migration coverage for business and checkpoint schema."""

from __future__ import annotations

from langgraph.checkpoint.postgres.base import BasePostgresSaver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

EXPECTED_TABLES = {
    "users",
    "orders",
    "service_statuses",
    "tickets",
    "ticket_knowledge_refs",
    "trace_events",
    "tool_call_records",
    "job_outbox",
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


async def table_names(connection: AsyncConnection) -> set[str]:
    result = await connection.scalar(
        text("select array_agg(tablename) from pg_tables where schemaname = 'public'")
    )
    return set(result or [])


async def test_upgrade_head_builds_business_and_checkpoint_schema(
    migrated_connection: AsyncConnection,
) -> None:
    tables = await table_names(migrated_connection)
    assert tables >= EXPECTED_TABLES


async def test_checkpoint_migration_versions_match_installed_library(
    migrated_connection: AsyncConnection,
) -> None:
    version = await migrated_connection.scalar(text("select max(v) from checkpoint_migrations"))
    assert version == len(BasePostgresSaver.MIGRATIONS) - 1


async def test_checkpoint_indexes_and_task_path_column_exist(
    migrated_connection: AsyncConnection,
) -> None:
    indexes = await migrated_connection.scalar(
        text("select array_agg(indexname) from pg_indexes where schemaname = 'public'")
    )
    assert {
        "checkpoints_thread_id_idx",
        "checkpoint_blobs_thread_id_idx",
        "checkpoint_writes_thread_id_idx",
    } <= set(indexes or [])

    columns = await migrated_connection.scalar(
        text(
            "select array_agg(column_name) from information_schema.columns "
            "where table_name = 'checkpoint_writes'"
        )
    )
    assert "task_path" in set(columns or [])
