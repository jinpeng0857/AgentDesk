"""Evaluation API contract routes."""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.dependencies import SessionDep
from backend.api.errors import ApiError
from backend.api.schemas import (
    EvalMetricResponse,
    EvalRunDetailResponse,
    EvalRunSummaryResponse,
)
from backend.db.repositories.eval_runs import EvalRunRepository

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


@router.get("/runs", response_model=list[EvalRunSummaryResponse])
async def list_eval_runs(session: SessionDep) -> list[EvalRunSummaryResponse]:
    repo = EvalRunRepository(session)
    runs = await repo.list_runs()
    return [
        EvalRunSummaryResponse(
            run_id=r.run_id,
            mode=r.mode,
            dataset_version=r.dataset_version,
            git_sha=r.git_sha,
            created_at=r.created_at,
        )
        for r in runs
    ]


@router.get(
    "/runs/{run_id}",
    response_model=EvalRunDetailResponse,
)
async def get_eval_run(run_id: str, session: SessionDep) -> EvalRunDetailResponse:
    repo = EvalRunRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise ApiError(
            status_code=404,
            code="EVAL_RUN_NOT_FOUND",
            message=f"Evaluation run '{run_id}' not found",
        )
    return EvalRunDetailResponse(
        run_id=run.run_id,
        mode=run.mode,
        dataset_version=run.dataset_version,
        git_sha=run.git_sha,
        created_at=run.created_at,
        model=run.model,
        prompt_versions=run.prompt_versions,
        metrics=[EvalMetricResponse(**m) for m in run.metrics],
    )
