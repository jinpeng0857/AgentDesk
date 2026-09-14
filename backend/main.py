"""FastAPI contract application for AgentDesk Phase 0."""

from typing import Any

from fastapi import FastAPI

from backend.api import approvals, auth, eval, health, tickets
from backend.api.stubs import ContractStubError, contract_stub_exception_handler


class ContractFastAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        schema["x-agentdesk-status"] = "contract-only"
        return schema


app = ContractFastAPI(
    title="AgentDesk API",
    version="0.2.0-draft",
    description="Contract-only API. Business execution starts in Phase 1.",
)
app.add_exception_handler(ContractStubError, contract_stub_exception_handler)
app.include_router(auth.router)
app.include_router(tickets.router)
app.include_router(approvals.router)
app.include_router(eval.router)
app.include_router(health.router)
