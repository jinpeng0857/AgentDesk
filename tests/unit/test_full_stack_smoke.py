"""The full-stack smoke command must fail at the first unhealthy contract."""

from __future__ import annotations

import httpx
import pytest

from scripts.acceptance import full_stack_smoke


@pytest.mark.parametrize(
    ("failed_path", "failed_status"),
    [
        ("/ready", 503),
        ("/api/auth/login", 503),
        ("/api/tickets", 401),
        ("/api/eval/runs", 503),
    ],
)
def test_smoke_exits_nonzero_when_a_required_check_fails(
    failed_path: str,
    failed_status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/ready":
            status = failed_status if failed_path == "/ready" else 200
            return httpx.Response(status, json={"status": "ok"})
        if request.url.path == "/api/auth/login":
            if failed_path == request.url.path:
                return httpx.Response(failed_status, json={"detail": "unavailable"})
            return httpx.Response(200, json={"access_token": "test-token"})
        if request.url.path == "/api/tickets":
            if failed_path == request.url.path:
                return httpx.Response(failed_status, json={"detail": "unauthorized"})
            return httpx.Response(202, json={"ticket_id": "ticket-test"})
        if request.url.path == "/api/tickets/ticket-test":
            return httpx.Response(200, json={"ticket_id": "ticket-test", "status": "resolved"})
        if request.url.path == "/api/eval/runs":
            status = failed_status if failed_path == request.url.path else 200
            return httpx.Response(status, json=[])
        return httpx.Response(404)

    transport = httpx.MockTransport(respond)
    client_type = httpx.Client

    def client_factory(*args: object, **kwargs: object) -> httpx.Client:
        return client_type(*args, transport=transport, **kwargs)

    monkeypatch.setattr(full_stack_smoke.httpx, "Client", client_factory)

    with pytest.raises(SystemExit) as error:
        full_stack_smoke.run_smoke("http://agentdesk.test", completion_timeout_s=1.0)

    assert error.value.code == 1
    assert requested_paths[-1] == failed_path
