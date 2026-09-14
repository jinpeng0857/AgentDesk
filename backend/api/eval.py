"""Evaluation API contract routes."""

from fastapi import APIRouter

from backend.api.schemas import (
    EvalRunDetailResponse,
    EvalRunSummaryResponse,
)
from backend.api.stubs import STUB_RESPONSE, raise_not_implemented

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


@router.get("/runs", response_model=list[EvalRunSummaryResponse], responses=STUB_RESPONSE)
async def list_eval_runs() -> list[EvalRunSummaryResponse]:
    raise_not_implemented()


@router.get(
    "/runs/{run_id}",
    response_model=EvalRunDetailResponse,
    responses=STUB_RESPONSE,
)
async def get_eval_run(run_id: str) -> EvalRunDetailResponse:
    del run_id
    raise_not_implemented()
