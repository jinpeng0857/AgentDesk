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
    operation = schema["paths"]["/api/tickets"]["post"]

    assert "ErrorEnvelope" in schema["components"]["schemas"]
    assert "501" in operation["responses"]
    assert schema["x-agentdesk-status"] == "phase-1-partial"


def test_auth_is_real_while_approvals_and_eval_remain_stubs() -> None:
    schema = _openapi()

    for path, method in (("/api/auth/login", "post"), ("/api/auth/me", "get")):
        assert "501" not in schema["paths"][path][method]["responses"], path

    for path, method in (
        ("/api/approvals", "get"),
        ("/api/approvals/{approval_id}", "get"),
        ("/api/approvals/{approval_id}/decision", "post"),
        ("/api/eval/runs", "get"),
        ("/api/eval/runs/{run_id}", "get"),
    ):
        assert "501" in schema["paths"][path][method]["responses"], path


def test_ticket_requester_identity_cannot_be_supplied_by_client() -> None:
    properties = _openapi()["components"]["schemas"]["TicketCreateRequest"]["properties"]

    assert set(properties) == {"user_query"}


def test_approval_decision_requires_version_and_cannot_edit_arguments() -> None:
    schema = _openapi()["components"]["schemas"]["ApprovalDecisionRequest"]

    assert set(schema["required"]) == {"decision", "version"}
    assert set(schema["properties"]) == {"decision", "version", "reason"}


async def test_health_is_live_but_business_routes_are_explicit_stubs() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        ticket = await client.post(
            "/api/tickets",
            json={"user_query": "VPN is unavailable"},
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ticket.status_code == 501
    assert ticket.json()["error"]["code"] == "NOT_IMPLEMENTED"
