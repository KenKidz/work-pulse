"""WorkPulse API — application entrypoint.

Run locally:  uv run uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.config import get_settings
from app.infrastructure.clickhouse.client import dispose_clickhouse
from app.infrastructure.clickhouse.schema import ensure_clickhouse_schema_best_effort
from app.shared.errors import register_error_handlers
from app.shared.logging import RequestContextMiddleware, configure_logging
from app.shared.telemetry import (
    METRICS_PATH,
    TelemetryMiddleware,
    configure_telemetry,
    get_telemetry,
)

settings = get_settings()
configure_logging(settings.environment)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Best-effort analytics DDL (ADR 0019 decision 11) + telemetry drain.

    ClickHouse is optional for the write path: a failure is logged as an error
    type only and boot continues; report reads then answer `503
    reports_unavailable`. On shutdown the OTel providers are flushed (never
    shut down: they are process-global and a later app instance in the same
    process — tests, TestClient — would lose telemetry, ADR 0023).
    """
    await to_thread.run_sync(ensure_clickhouse_schema_best_effort)
    try:
        yield
    finally:
        telemetry = get_telemetry()
        if telemetry is not None:
            # The SDK force-flush is a blocking export; keep it off the event
            # loop so shutdown never stalls live traffic behind an OTLP
            # collector (rule 6).
            await to_thread.run_sync(telemetry.flush)
        await to_thread.run_sync(dispose_clickhouse)


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Kept as a factory so tests and future workers can create isolated instances.
    `configure_telemetry` is idempotent and runs here (not only in the
    lifespan) so in-process clients — tests, ASGI transports — that skip
    lifespan startup still get spans, metrics and log correlation.
    """
    configure_telemetry(settings)

    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.environment == "development",
        lifespan=lifespan,
    )

    # Starlette wraps in reverse registration order: the last middleware added
    # is outermost. CORS stays outermost; telemetry wraps the request-id
    # logging middleware so the `request` log line runs inside the server span
    # and carries `trace_id`/`span_id` (ADR 0023).
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(TelemetryMiddleware)
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

    @application.get(METRICS_PATH, include_in_schema=False)
    def metrics() -> Response:
        """Prometheus text exposition (ADR 0023).

        Operational plumbing like `/healthz`: unauthenticated, aggregate and
        tenant-agnostic only, and deliberately hidden from the OpenAPI
        contract so `contracts/openapi/openapi.json` and the generated client
        never drift (business rule 7).
        """
        telemetry = get_telemetry()
        payload = telemetry.metrics_text() if telemetry is not None else b""
        return Response(
            content=payload,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
