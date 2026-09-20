"""PostgreSQL persistence models.

Domain enums are stored as constrained strings so a migration sees an explicit
value set instead of an implicit database type. Every table below is created by
an Alembic revision; no runtime path calls ``create_all()``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base

TICKET_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "resolved",
    "escalated",
    "rejected",
    "failed",
    "expired",
)
INTENTS = ("network", "account", "permission", "query", "high_risk")
RISK_LEVELS = ("low", "medium", "high")
RESOLUTION_MODES = ("automated", "fallback", "human_required")
ACTORS = ("supervisor", "knowledge", "action", "policy", "approval", "dispatcher")
TOOL_OUTCOMES = (
    "succeeded",
    "failed_retryable",
    "failed_final",
    "rejected",
    "timed_out",
    "cache_hit",
)
USER_ROLES = ("requester", "approver")
ORDER_STATUSES = ("pending", "paid", "refunded", "cancelled")
SERVICE_STATUSES = ("operational", "degraded", "outage")
TOOL_OPERATION_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed_retryable",
    "failed_final",
    "rejected",
)
APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")
REFUND_STATUSES = ("succeeded", "failed")
ACCESS_REQUEST_STATUSES = ("created", "approved", "rejected")
DOWNSTREAM_TICKET_STATUSES = ("created", "resolved", "failed")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def _utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in_list("role", USER_ROLES), name="role_valid"),
        CheckConstraint("version >= 1", name="user_version_positive"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    masked_email: Mapped[str] = mapped_column(String(254))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class OrderRecord(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_in_list("status", ORDER_STATUSES), name="status_valid"),
        CheckConstraint("currency = 'USD'", name="currency_usd"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    refundable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ServiceStatusRecord(Base):
    __tablename__ = "service_statuses"
    __table_args__ = (
        CheckConstraint(_in_list("status", SERVICE_STATUSES), name="status_valid"),
    )

    service_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    incident_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TicketRecord(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(_in_list("status", TICKET_STATUSES), name="status_valid"),
        CheckConstraint(
            "intent IS NULL OR " + _in_list("intent", INTENTS), name="intent_valid"
        ),
        CheckConstraint(
            "risk_level IS NULL OR " + _in_list("risk_level", RISK_LEVELS),
            name="risk_level_valid",
        ),
        CheckConstraint(
            "resolution_mode IS NULL OR " + _in_list("resolution_mode", RESOLUTION_MODES),
            name="resolution_mode_valid",
        ),
        CheckConstraint("step_count >= 0", name="step_count_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[str] = mapped_column(String(36), unique=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    user_query: Mapped[str] = mapped_column(String(2_000))
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), index=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_response: Mapped[str | None] = mapped_column(String(4_000), nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


class TicketKnowledgeRefRecord(Base):
    __tablename__ = "ticket_knowledge_refs"
    __table_args__ = (
        UniqueConstraint("ticket_id", "chunk_id", name="ticket_chunk"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[str] = mapped_column(String(128))
    chunk_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200))
    content_excerpt: Mapped[str] = mapped_column(String(1_000))
    score: Mapped[float] = mapped_column(Float)


class TraceEventRecord(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        CheckConstraint(_in_list("actor", ACTORS), name="actor_valid"),
        UniqueConstraint("ticket_id", "request_id", name="ticket_request"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    actor: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(String(1_000))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ToolCallRecord(Base):
    __tablename__ = "tool_call_records"
    __table_args__ = (
        CheckConstraint(_in_list("outcome", TOOL_OUTCOMES), name="outcome_valid"),
        CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    operation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str] = mapped_column(String(32))
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    args_summary: Mapped[dict[str, object]] = mapped_column(JSONB)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    outcome: Mapped[str] = mapped_column(String(24))
    latency_ms: Mapped[int] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class JobOutboxRecord(Base):
    __tablename__ = "job_outbox"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    job_name: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class ToolOperationRecord(Base):
    __tablename__ = "tool_operations"
    __table_args__ = (
        CheckConstraint(_in_list("status", TOOL_OPERATION_STATUSES), name="tool_op_status_valid"),
        UniqueConstraint("idempotency_key", name="tool_op_idempotency_key_unique"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    operation_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    args_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    lease_holder: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    encrypted_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(_in_list("status", APPROVAL_STATUSES), name="approval_status_valid"),
        CheckConstraint(_in_list("risk_level", RISK_LEVELS), name="approval_risk_level_valid"),
        CheckConstraint("version >= 1", name="approval_version_positive"),
        UniqueConstraint("operation_id", name="approval_operation_unique"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    operation_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), index=True, default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    args_hash: Mapped[str] = mapped_column(String(64))
    args_summary: Mapped[list[dict[str, object]] | dict[str, object]] = mapped_column(JSONB)
    reason_code: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    approver_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefundRecord(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount > 0", name="refund_amount_positive"),
        CheckConstraint("currency = 'USD'", name="refund_currency_usd"),
        CheckConstraint(_in_list("status", REFUND_STATUSES), name="refund_status_valid"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(32), default="succeeded")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class AccessRequestRecord(Base):
    __tablename__ = "access_requests"
    __table_args__ = (
        CheckConstraint(
            _in_list("status", ACCESS_REQUEST_STATUSES), name="access_request_status_valid"
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    resource: Mapped[str] = mapped_column(String(200))
    justification: Mapped[str] = mapped_column(String(2_000))
    status: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class DownstreamTicketRecord(Base):
    __tablename__ = "downstream_tickets"
    __table_args__ = (
        CheckConstraint(
            _in_list("status", DOWNSTREAM_TICKET_STATUSES),
            name="downstream_ticket_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2_000))
    status: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class EvalRunRecord(Base):
    __tablename__ = "eval_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    git_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

