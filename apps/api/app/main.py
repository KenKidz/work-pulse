"""WorkPulse API — application entrypoint.

Run locally:  uv run uvicorn app.main:app --reload
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import to_thread
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import api_router
from app.config import get_settings
from app.infrastructure.clickhouse.client import dispose_clickhouse
from app.infrastructure.clickhouse.schema import ensure_clickhouse_schema_best_effort
from app.infrastructure.postgres.engine import get_engine
from app.shared.boot import validate_boot_configuration
from app.shared.errors import DependencyUnavailableError, register_error_handlers
from app.shared.logging import (
    RequestContextMiddleware,
    configure_logging,
    get_logger,
)
from app.shared.rate_limit import RateLimiter, RateLimitMiddleware
from app.shared.telemetry import (
    METRICS_PATH,
    TelemetryMiddleware,
    configure_telemetry,
    get_telemetry,
)

settings = get_settings()
configure_logging(settings.environment)

_log = get_logger("main")


async def _probe_database() -> None:
    """Open a pooled connection and run the readiness query."""
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_database_ready(*, timeout_seconds: float | None = None) -> None:
    """Assert PostgreSQL answers within the readiness bound.

    Success returns `None`; any failure — connection refused, query error or
    the timeout — raises the house 503 `DependencyUnavailableError`. Failures
    are logged by exception type only: driver text can carry DSNs and server
    details (`.ai/security.md`), and the response names no dependency beyond
    the stable `dependency_unavailable` code.
    """
    bound = (
        settings.readyz_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    try:
        await asyncio.wait_for(_probe_database(), timeout=bound)
    except Exception as exc:
        _log.warning("readiness_probe_failed", error=type(exc).__name__)
        raise DependencyUnavailableError() from None


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
    validate_boot_configuration()
    configure_telemetry(settings)

    application = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.environment == "development",
        lifespan=lifespan,
    )

    # The limiter is app state (not a constructor constant) so tests can
    # install one with an injected clock and explicit budgets (ADR 0025).
    application.state.rate_limiter = RateLimiter.from_settings(settings)

    # Starlette wraps in reverse registration order: the last middleware added
    # is outermost. CORS stays outermost; telemetry wraps the request-id
    # logging middleware so the `request` log line runs inside the server span
    # and carries `trace_id`/`span_id` (ADR 0023). The rate limiter is
    # innermost so its 429 short-circuits are still observed by telemetry
    # (`workpulse_http_requests`) and the request log, while running before any
    # router/service work (Phase 7, ADR 0025).
    application.add_middleware(RateLimitMiddleware)
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

    @application.get("/readyz", tags=["meta"])
    async def readyz() -> dict[str, str]:
        """Readiness probe: PostgreSQL reachable within `READYZ_TIMEOUT_SECONDS`."""
        await check_database_ready()
        return {"status": "ready"}

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
