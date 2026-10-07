"""FastAPI application factory.

Importing this module must not connect to PostgreSQL, Redis or a model provider.
The engine is created inside the lifespan, and ``app.state.session_factory`` stays
``None`` until the process starts, which keeps OpenAPI generation offline-safe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from backend.api import approvals, auth, eval, health, tickets
from backend.api.errors import ApiError, api_error_handler, authentication_error_handler
from backend.api.stubs import ContractStubError, contract_stub_exception_handler
from backend.auth.jwt import AuthenticationError
from backend.config import AppSettings
from backend.db.session import async_session_factory, create_async_engine_from_settings
from backend.jobs.serialization import json_job_deserializer, json_job_serializer

OPENAPI_STATUS = "phase-1-partial"


class AgentDeskFastAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        schema["x-agentdesk-status"] = OPENAPI_STATUS
        return schema


def create_app(settings: AppSettings | None = None) -> FastAPI:
    resolved = settings or AppSettings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine_from_settings(resolved)
        application.state.engine = engine
        application.state.session_factory = async_session_factory(engine)

        redis = await create_pool(
            RedisSettings.from_dsn(resolved.redis_url),
            job_serializer=json_job_serializer,
            job_deserializer=json_job_deserializer,
        )
        application.state.redis = redis

        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    application = AgentDeskFastAPI(
        title="AgentDesk API",
        version="0.3.0",
        description=(
            "AgentDesk phase 1 read-only slice. Business write tools remain disabled "
            "until phase 2."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.session_factory = None
    application.state.redis = None

    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(AuthenticationError, authentication_error_handler)
    application.add_exception_handler(ContractStubError, contract_stub_exception_handler)

    application.include_router(auth.router)
    application.include_router(tickets.router)
    application.include_router(approvals.router)
    application.include_router(eval.router)
    application.include_router(health.router)
    return application


app = create_app()
