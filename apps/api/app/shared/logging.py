"""Structured logging (structlog, ADR 0006).

Pretty console output in development, JSON elsewhere. The request middleware
binds `request_id` and `tenant_id` (when the header is present) to every log
line of a request. Never log PII — ids and outcomes only (`.ai/security.md`).
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CONFIGURED = False


def configure_logging(environment: str = "development") -> None:
    """Idempotent structlog setup. Call once from the app factory."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer()
        if environment == "development"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str = "workpulse") -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a `request_id` (and `tenant_id`, when sent) to every request.

    The `X-Tenant-Id` header is logged as a raw string only for correlation —
    identity validation lives in `app.shared.identity`, never here.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        tenant_id = request.headers.get("X-Tenant-Id")
        if tenant_id:
            structlog.contextvars.bind_contextvars(tenant_id=tenant_id)

        log = get_logger("http")
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000

        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        response.headers["X-Request-Id"] = request_id
        return response
