from __future__ import annotations

import importlib

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

EXPECTED_OPERATIONS = {
    ("post", "/api/auth/login"),
    ("get", "/api/auth/me"),
    ("post", "/api/tickets"),
    ("get", "/api/tickets"),
    ("get", "/api/tickets/{ticket_id}"),
    ("get", "/api/tickets/{ticket_id}/trace"),
    ("get", "/api/approvals"),
    ("get", "/api/approvals/{approval_id}"),
    ("post", "/api/approvals/{approval_id}/decision"),
    ("get", "/api/eval/runs"),
    ("get", "/api/eval/runs/{run_id}"),
    ("get", "/health"),
    ("get", "/ready"),
}


def _app() -> FastAPI:
    module = importlib.import_module("backend.main")
    app = getattr(module, "app", None)
    assert isinstance(app, FastAPI), "FastAPI contract app is missing"
    return app


def _openapi() -> dict:
    return _app().openapi()


def test_openapi_exposes_exact_phase_zero_operations() -> None:
    schema = _openapi()
    actual = {
        (method, path)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }

    assert actual == EXPECTED_OPERATIONS


def test_ticket_creation_contract_is_async() -> None:
    operation = _openapi()["paths"]["/api/tickets"]["post"]

    assert "202" in operation["responses"]
    assert "200" not in operation["responses"]


def test_error_envelope_and_contract_status_are_published() -> None:
    schema = _openapi()

    assert "ErrorEnvelope" in schema["components"]["schemas"]
    assert "501" not in schema["paths"]["/api/approvals"]["get"]["responses"]
    assert "501" not in schema["paths"]["/api/eval/runs"]["get"]["responses"]
    assert "501" not in schema["paths"]["/api/tickets"]["post"]["responses"]
    assert "202" in schema["paths"]["/api/tickets"]["post"]["responses"]
    assert schema["x-agentdesk-status"] == "phase-1-partial"


def test_auth_approvals_and_eval_are_real() -> None:
    schema = _openapi()

    for path, method in (
        ("/api/auth/login", "post"),
        ("/api/auth/me", "get"),
        ("/api/approvals", "get"),
        ("/api/approvals/{approval_id}", "get"),
        ("/api/approvals/{approval_id}/decision", "post"),
        ("/api/eval/runs", "get"),
        ("/api/eval/runs/{run_id}", "get"),
    ):
        assert "501" not in schema["paths"][path][method]["responses"], path


def test_ticket_requester_identity_cannot_be_supplied_by_client() -> None:
    properties = _openapi()["components"]["schemas"]["TicketCreateRequest"]["properties"]

    assert set(properties) == {"user_query"}


def test_approval_decision_requires_version_and_cannot_edit_arguments() -> None:
    schema = _openapi()["components"]["schemas"]["ApprovalDecisionRequest"]

    assert set(schema["required"]) == {"decision", "version"}
    assert set(schema["properties"]) == {"decision", "version", "reason"}


async def test_health_is_live_and_unimplemented_routes_are_explicit_stubs() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        eval_run = await client.get("/api/eval/runs")
        approval = await client.get("/api/approvals")
        ticket = await client.post(
            "/api/tickets",
            json={"user_query": "VPN is unavailable"},
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    # Eval, approval and ticket routes are real now. This app instance has no lifespan, so the
    # session factory is absent and requests fail as not-ready rather than
    # reaching the database check.
    assert eval_run.status_code == 503
    assert eval_run.json()["error"]["code"] == "NOT_READY"
    assert approval.status_code == 503
    assert approval.json()["error"]["code"] == "NOT_READY"
    assert ticket.status_code == 503
    assert ticket.json()["error"]["code"] == "NOT_READY"

