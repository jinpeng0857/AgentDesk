"""Approval API contract routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from agents.schemas import ApprovalStatus, RiskLevel
from backend.api.dependencies import SessionDep
from backend.api.errors import ApiError
from backend.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDetailResponse,
    ApprovalSummaryResponse,
    ErrorEnvelope,
    RedactedArgument,
)
from backend.auth.dependencies import ApproverActor
from backend.db.repositories.approvals import ApprovalConflictError, ApprovalRepository
from backend.db.repositories.operations import OperationRepository
from backend.db.repositories.outbox import OutboxRepository
from backend.jobs.tickets import resume_job_id

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

APPROVAL_QUERY_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    403: {"model": ErrorEnvelope, "description": "Forbidden"},
}

APPROVAL_DETAIL_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    403: {"model": ErrorEnvelope, "description": "Forbidden"},
    404: {"model": ErrorEnvelope, "description": "Approval not found"},
}

APPROVAL_DECISION_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    403: {"model": ErrorEnvelope, "description": "Forbidden"},
    404: {"model": ErrorEnvelope, "description": "Approval not found"},
    409: {"model": ErrorEnvelope, "description": "Version conflict or expired"},
}


@router.get("", response_model=list[ApprovalSummaryResponse], responses=APPROVAL_QUERY_RESPONSES)
async def list_approvals(
    actor: ApproverActor,
    session: SessionDep,
    status: ApprovalStatus | None = None,
) -> list[ApprovalSummaryResponse]:
    del actor
    repo = ApprovalRepository(session)
    await repo.expire_pending()
    records = await repo.list_all(status=str(status) if status else None)
    return [
        ApprovalSummaryResponse(
            approval_id=str(record.id),
            ticket_id=str(record.ticket_id),
            operation_id=record.operation_id,
            tool_name=record.tool_name,
            risk_level=RiskLevel(record.risk_level),
            status=ApprovalStatus(record.status),
            version=record.version,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )
        for record in records
    ]


@router.get(
    "/{approval_id}",
    response_model=ApprovalDetailResponse,
    responses=APPROVAL_DETAIL_RESPONSES,
)
async def get_approval(
    approval_id: str,
    actor: ApproverActor,
    session: SessionDep,
) -> ApprovalDetailResponse:
    del actor
    try:
        approval_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise ApiError(
            status_code=404,
            code="APPROVAL_NOT_FOUND",
            message="Approval not found",
        ) from None

    repo = ApprovalRepository(session)
    await repo.expire_pending()
    record = await repo.get_by_id(approval_uuid)
    if record is None:
        raise ApiError(
            status_code=404,
            code="APPROVAL_NOT_FOUND",
            message="Approval not found",
        )

    args_summary: list[RedactedArgument] = []
    if isinstance(record.args_summary, list):
        for item in record.args_summary:
            if "name" in item and "value" in item:
                args_summary.append(
                    RedactedArgument(name=str(item["name"]), value=str(item["value"]))
                )
            else:
                for k, v in item.items():
                    args_summary.append(RedactedArgument(name=str(k), value=str(v)))
    elif isinstance(record.args_summary, dict):
        for k, v in record.args_summary.items():
            args_summary.append(RedactedArgument(name=str(k), value=str(v)))

    return ApprovalDetailResponse(
        approval_id=str(record.id),
        ticket_id=str(record.ticket_id),
        operation_id=record.operation_id,
        tool_name=record.tool_name,
        risk_level=RiskLevel(record.risk_level),
        status=ApprovalStatus(record.status),
        version=record.version,
        created_at=record.created_at,
        expires_at=record.expires_at,
        args_hash=record.args_hash,
        args_summary=args_summary,
        reason_code=record.reason_code,
    )


@router.post(
    "/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    responses=APPROVAL_DECISION_RESPONSES,
)
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    actor: ApproverActor,
    session: SessionDep,
) -> ApprovalDecisionResponse:
    try:
        approval_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise ApiError(
            status_code=404,
            code="APPROVAL_NOT_FOUND",
            message="Approval not found",
        ) from None

    repo = ApprovalRepository(session)
    existing = await repo.get_by_id(approval_uuid)
    if existing is None:
        raise ApiError(
            status_code=404,
            code="APPROVAL_NOT_FOUND",
            message="Approval not found",
        )

    now = datetime.now(UTC)
    if existing.status == "expired" or (
        existing.status == "pending" and existing.expires_at <= now
    ):
        await repo.expire_pending()
        await session.commit()
        raise ApiError(
            status_code=409,
            code="APPROVAL_EXPIRED",
            message="Approval has expired",
        )

    if existing.status != "pending":
        raise ApiError(
            status_code=409,
            code="APPROVAL_CONFLICT",
            message=f"Approval has already transitioned to {existing.status}",
        )

    if existing.version != payload.version:
        raise ApiError(
            status_code=409,
            code="APPROVAL_CONFLICT",
            message=f"Version mismatch: expected {existing.version}, got {payload.version}",
        )

    op_repo = OperationRepository(session)
    op_record = await op_repo.get_by_operation_id(existing.operation_id)
    if op_record is not None and op_record.args_hash != existing.args_hash:
        raise ApiError(
            status_code=409,
            code="ARGS_HASH_MISMATCH",
            message="Operation args hash does not match approval",
        )

    try:
        decided = await repo.decide(
            approval_uuid,
            expected_version=payload.version,
            decision=payload.decision,
            approver_id=actor.user_id,
            reason=payload.reason,
        )
    except ApprovalConflictError as exc:
        if "expired" in str(exc).lower():
            raise ApiError(status_code=409, code="APPROVAL_EXPIRED", message=str(exc)) from exc
        raise ApiError(status_code=409, code="APPROVAL_CONFLICT", message=str(exc)) from exc

    if decided.status == "approved":
        outbox = OutboxRepository(session)
        dedupe = resume_job_id(str(decided.id), decided.version)
        await outbox.enqueue_resume_ticket(
            ticket_id=decided.ticket_id,
            approval_id=decided.id,
            dedupe_key=dedupe,
        )

    await session.commit()

    return ApprovalDecisionResponse(
        approval_id=str(decided.id),
        status="approved" if decided.status == "approved" else "rejected",
        version=decided.version,
    )

