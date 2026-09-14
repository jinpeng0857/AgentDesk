"""Approval API contract routes."""

from fastapi import APIRouter

from backend.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDetailResponse,
    ApprovalSummaryResponse,
)
from backend.api.stubs import STUB_RESPONSE, raise_not_implemented

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalSummaryResponse], responses=STUB_RESPONSE)
async def list_approvals() -> list[ApprovalSummaryResponse]:
    raise_not_implemented()


@router.get(
    "/{approval_id}",
    response_model=ApprovalDetailResponse,
    responses=STUB_RESPONSE,
)
async def get_approval(approval_id: str) -> ApprovalDetailResponse:
    del approval_id
    raise_not_implemented()


@router.post(
    "/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    responses=STUB_RESPONSE,
)
async def decide_approval(
    approval_id: str,
    _: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    del approval_id
    raise_not_implemented()
