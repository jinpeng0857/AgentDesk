"""phase2 governance and hitl

Revision ID: 20260916_0002
Revises: 20260914_0001
Create Date: 2026-09-16

Creates the Phase 2 governance ledger tables (tool_operations, approvals) and
business write result tables (refunds, access_requests, downstream_tickets),
and adds version to users.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260916_0002"
down_revision: str | None = "20260914_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users: add version column and check constraint
    op.add_column(
        "users",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "user_version_positive",
        "users",
        "version >= 1",
    )

    # 2. tool_operations
    op.create_table(
        "tool_operations",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("lease_holder", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encrypted_result", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "status IN ('pending', 'running', 'succeeded', "
            "'failed_retryable', 'failed_final', 'rejected')",
            name="tool_op_status_valid",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="tool_op_idempotency_key_unique"),
    )
    op.create_index(
        op.f("ix_tool_operations_idempotency_key"),
        "tool_operations",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_tool_operations_operation_id"),
        "tool_operations",
        ["operation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_operations_status"),
        "tool_operations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_operations_ticket_id"),
        "tool_operations",
        ["ticket_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_operations_tool_name"),
        "tool_operations",
        ["tool_name"],
        unique=False,
    )

    # 3. approvals
    op.create_table(
        "approvals",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("args_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("approver_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="approval_status_valid",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="approval_risk_level_valid",
        ),
        sa.CheckConstraint("version >= 1", name="approval_version_positive"),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="approval_operation_unique"),
    )
    op.create_index(op.f("ix_approvals_operation_id"), "approvals", ["operation_id"], unique=True)
    op.create_index(op.f("ix_approvals_status"), "approvals", ["status"], unique=False)
    op.create_index(op.f("ix_approvals_ticket_id"), "approvals", ["ticket_id"], unique=False)

    # 4. refunds
    op.create_table(
        "refunds",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("ticket_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=False),
        sa.Column("requester_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="succeeded"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="refund_amount_positive"),
        sa.CheckConstraint("currency = 'USD'", name="refund_currency_usd"),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="refund_status_valid",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_refunds_order_id"), "refunds", ["order_id"], unique=False)
    op.create_index(op.f("ix_refunds_requester_id"), "refunds", ["requester_id"], unique=False)
    op.create_index(op.f("ix_refunds_ticket_id"), "refunds", ["ticket_id"], unique=False)

    # 5. access_requests
    op.create_table(
        "access_requests",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("ticket_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_id", sa.String(length=128), nullable=False),
        sa.Column("resource", sa.String(length=200), nullable=False),
        sa.Column("justification", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('created', 'approved', 'rejected')",
            name="access_request_status_valid",
        ),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_access_requests_requester_id"),
        "access_requests",
        ["requester_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_access_requests_ticket_id"),
        "access_requests",
        ["ticket_id"],
        unique=False,
    )

    # 6. downstream_tickets
    op.create_table(
        "downstream_tickets",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("source_ticket_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('created', 'resolved', 'failed')",
            name="downstream_ticket_status_valid",
        ),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_downstream_tickets_requester_id"),
        "downstream_tickets",
        ["requester_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_downstream_tickets_source_ticket_id"),
        "downstream_tickets",
        ["source_ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("downstream_tickets")
    op.drop_table("access_requests")
    op.drop_table("refunds")
    op.drop_table("approvals")
    op.drop_table("tool_operations")
    op.drop_constraint("user_version_positive", "users", type_="check")
    op.drop_column("users", "version")
