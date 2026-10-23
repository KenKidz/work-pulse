"""WorkPulse API — application entrypoint.

Run locally:  uv run uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.config import get_settings
from app.infrastructure.clickhouse.client import dispose_clickhouse
from app.infrastructure.clickhouse.schema import ensure_clickhouse_schema_best_effort
from app.shared.errors import register_error_handlers
from app.shared.logging import RequestContextMiddleware, configure_logging

settings = get_settings()
configure_logging(settings.environment)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Best-effort analytics DDL (ADR 0019 decision 11).

    ClickHouse is optional for the write path: a failure is logged as an error
    type only and boot continues; report reads then answer `503
    reports_unavailable`.
    """
    await to_thread.run_sync(ensure_clickhouse_schema_best_effort)
    try:
        yield
    finally:
        await to_thread.run_sync(dispose_clickhouse)


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Kept as a factory so tests and future workers can create isolated instances.
    """
    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.environment == "development",
        lifespan=lifespan,
    )

    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(application)

    @application.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        """Liveness probe. Must not depend on external services."""
        return {"status": "ok", "version": settings.version}

    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
