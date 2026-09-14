"""phase1 foundation

Revision ID: 20260914_0001
Revises:
Create Date: 2026-09-14

Creates the business projection tables and the LangGraph checkpoint tables.

The checkpoint DDL is a frozen copy of the SQL shipped in the installed
``BasePostgresSaver.MIGRATIONS``. Runtime code must never call
``AsyncPostgresSaver.setup()``; the integration assertion
``test_checkpoint_migration_versions_match_installed_library`` fails when a
dependency upgrade adds a migration that this revision has not encoded yet.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260914_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKPOINT_MIGRATIONS: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)",
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        parent_checkpoint_id TEXT,
        type TEXT,
        checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint_blobs (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL,
        version TEXT NOT NULL,
        type TEXT NOT NULL,
        blob BYTEA,
        PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoint_writes (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        channel TEXT NOT NULL,
        type TEXT,
        blob BYTEA NOT NULL,
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    )
    """,
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null",
    "SELECT 1",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx "
    "ON checkpoints(thread_id)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx "
    "ON checkpoint_blobs(thread_id)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx "
    "ON checkpoint_writes(thread_id)",
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''",
)


def _apply_checkpoint_schema() -> None:
    context = op.get_context()
    for version, statement in enumerate(CHECKPOINT_MIGRATIONS):
        if "CONCURRENTLY" in statement:
            # Concurrent index builds cannot run inside a transaction block.
            with context.autocommit_block():
                op.execute(statement)
        else:
            op.execute(statement)
        op.execute(
            sa.text(
                "INSERT INTO checkpoint_migrations (v) VALUES (:version) ON CONFLICT DO NOTHING"
            ).bindparams(version=version)
        )


def upgrade() -> None:
    op.create_table(
        "service_statuses",
        sa.Column("service_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("incident_id", sa.String(length=128), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('operational', 'degraded', 'outage')",
            name=op.f("ck_service_statuses_status_valid"),
        ),
        sa.PrimaryKeyConstraint("service_name", name=op.f("pk_service_statuses")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("masked_email", sa.String(length=254), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('requester', 'approver')", name=op.f("ck_users_role_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("requester_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("refundable_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("currency = 'USD'", name=op.f("ck_orders_currency_usd")),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'refunded', 'cancelled')",
            name=op.f("ck_orders_status_valid"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_orders_version_positive")),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["users.id"], name=op.f("fk_orders_requester_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(op.f("ix_orders_requester_id"), "orders", ["requester_id"], unique=False)
    op.create_table(
        "tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=128), nullable=False),
        sa.Column("user_query", sa.String(length=2000), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=True),
        sa.Column("resolution_mode", sa.String(length=32), nullable=True),
        sa.Column("final_response", sa.String(length=4000), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "intent IS NULL OR intent IN "
            "('network', 'account', 'permission', 'query', 'high_risk')",
            name=op.f("ck_tickets_intent_valid"),
        ),
        sa.CheckConstraint(
            "resolution_mode IS NULL OR resolution_mode IN "
            "('automated', 'fallback', 'human_required')",
            name=op.f("ck_tickets_resolution_mode_valid"),
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('low', 'medium', 'high')",
            name=op.f("ck_tickets_risk_level_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'resolved', "
            "'escalated', 'rejected', 'failed', 'expired')",
            name=op.f("ck_tickets_status_valid"),
        ),
        sa.CheckConstraint("step_count >= 0", name=op.f("ck_tickets_step_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["users.id"], name=op.f("fk_tickets_requester_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("thread_id", name=op.f("uq_tickets_thread_id")),
        sa.UniqueConstraint("trace_id", name=op.f("uq_tickets_trace_id")),
    )
    op.create_index(op.f("ix_tickets_requester_id"), "tickets", ["requester_id"], unique=False)
    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"], unique=False)
    op.create_table(
        "job_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_job_outbox_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_job_outbox_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_outbox")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_job_outbox_dedupe_key")),
    )
    op.create_index(op.f("ix_job_outbox_ticket_id"), "job_outbox", ["ticket_id"], unique=False)
    op.create_table(
        "ticket_knowledge_refs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_excerpt", sa.String(length=1000), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "score >= 0 AND score <= 1", name=op.f("ck_ticket_knowledge_refs_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_knowledge_refs_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_knowledge_refs")),
        sa.UniqueConstraint("ticket_id", "chunk_id", name="ticket_chunk"),
    )
    op.create_index(
        op.f("ix_ticket_knowledge_refs_ticket_id"),
        "ticket_knowledge_refs",
        ["ticket_id"],
        unique=False,
    )
    op.create_table(
        "tool_call_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=True),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("args_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed_retryable', 'failed_final', "
            "'rejected', 'timed_out', 'cache_hit')",
            name=op.f("ck_tool_call_records_outcome_valid"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0", name=op.f("ck_tool_call_records_latency_non_negative")
        ),
        sa.CheckConstraint(
            "retry_count >= 0", name=op.f("ck_tool_call_records_retry_count_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_tool_call_records_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_call_records")),
    )
    op.create_index(
        op.f("ix_tool_call_records_request_id"), "tool_call_records", ["request_id"], unique=False
    )
    op.create_index(
        op.f("ix_tool_call_records_ticket_id"), "tool_call_records", ["ticket_id"], unique=False
    )
    op.create_index(
        op.f("ix_tool_call_records_tool_name"), "tool_call_records", ["tool_name"], unique=False
    )
    op.create_index(
        op.f("ix_tool_call_records_trace_id"), "tool_call_records", ["trace_id"], unique=False
    )
    op.create_table(
        "trace_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor IN ('supervisor', 'knowledge', 'action', 'policy', "
            "'approval', 'dispatcher')",
            name=op.f("ck_trace_events_actor_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_trace_events_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trace_events")),
        sa.UniqueConstraint("ticket_id", "request_id", name="ticket_request"),
    )
    op.create_index(
        op.f("ix_trace_events_request_id"), "trace_events", ["request_id"], unique=False
    )
    op.create_index(op.f("ix_trace_events_ticket_id"), "trace_events", ["ticket_id"], unique=False)
    op.create_index(op.f("ix_trace_events_trace_id"), "trace_events", ["trace_id"], unique=False)

    _apply_checkpoint_schema()


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS checkpoint_writes")
    op.execute("DROP TABLE IF EXISTS checkpoint_blobs")
    op.execute("DROP TABLE IF EXISTS checkpoints")
    op.execute("DROP TABLE IF EXISTS checkpoint_migrations")

    op.drop_index(op.f("ix_trace_events_trace_id"), table_name="trace_events")
    op.drop_index(op.f("ix_trace_events_ticket_id"), table_name="trace_events")
    op.drop_index(op.f("ix_trace_events_request_id"), table_name="trace_events")
    op.drop_table("trace_events")
    op.drop_index(op.f("ix_tool_call_records_trace_id"), table_name="tool_call_records")
    op.drop_index(op.f("ix_tool_call_records_tool_name"), table_name="tool_call_records")
    op.drop_index(op.f("ix_tool_call_records_ticket_id"), table_name="tool_call_records")
    op.drop_index(op.f("ix_tool_call_records_request_id"), table_name="tool_call_records")
    op.drop_table("tool_call_records")
    op.drop_index(op.f("ix_ticket_knowledge_refs_ticket_id"), table_name="ticket_knowledge_refs")
    op.drop_table("ticket_knowledge_refs")
    op.drop_index(op.f("ix_job_outbox_ticket_id"), table_name="job_outbox")
    op.drop_table("job_outbox")
    op.drop_index(op.f("ix_tickets_status"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_requester_id"), table_name="tickets")
    op.drop_table("tickets")
    op.drop_index(op.f("ix_orders_requester_id"), table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
    op.drop_table("service_statuses")
