"""Unit tests for Eval API endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_session
from backend.db.models import EvalRunRecord
from backend.main import create_app


@pytest.fixture
def mock_eval_run() -> EvalRunRecord:
    return EvalRunRecord(
        run_id="run-test-12345",
        mode="offline",
        dataset_version="1.0.0",
        git_sha="abcdef1234567890abcdef1234567890abcdef12",
        model="fake-model",
        prompt_versions={"supervisor": "1.0.0", "knowledge": "1.0.0"},
        metrics=[
            {
                "name": "intent_accuracy",
                "value": "0.95",
                "unit": "ratio",
            },
            {
                "name": "high_risk_bypass_count",
                "value": "0",
                "unit": "count",
            },
        ],
        created_at=datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_list_eval_runs(mock_eval_run: EvalRunRecord) -> None:
    app = create_app()

    mock_session = AsyncMock(spec=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session

    with patch(
        "backend.api.eval.EvalRunRepository.list_runs",
        new_callable=AsyncMock,
        return_value=[mock_eval_run],
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/eval/runs")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["run_id"] == "run-test-12345"
    assert data[0]["mode"] == "offline"
    assert data[0]["dataset_version"] == "1.0.0"
    assert data[0]["git_sha"] == "abcdef1234567890abcdef1234567890abcdef12"


@pytest.mark.asyncio
async def test_get_eval_run_success(mock_eval_run: EvalRunRecord) -> None:
    app = create_app()

    mock_session = AsyncMock(spec=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session

    with patch(
        "backend.api.eval.EvalRunRepository.get_run",
        new_callable=AsyncMock,
        return_value=mock_eval_run,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/eval/runs/run-test-12345")

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == "run-test-12345"
    assert data["mode"] == "offline"
    assert data["model"] == "fake-model"
    assert len(data["metrics"]) == 2
    assert data["metrics"][0]["name"] == "intent_accuracy"
    assert float(data["metrics"][0]["value"]) == 0.95
    assert data["metrics"][0]["unit"] == "ratio"


@pytest.mark.asyncio
async def test_get_eval_run_not_found() -> None:
    app = create_app()

    mock_session = AsyncMock(spec=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session

    with patch(
        "backend.api.eval.EvalRunRepository.get_run",
        new_callable=AsyncMock,
        return_value=None,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/eval/runs/non-existent-run")

    assert response.status_code == 404
    err = response.json()
    assert err["error"]["code"] == "EVAL_RUN_NOT_FOUND"
