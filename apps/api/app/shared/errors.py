"""Cross-domain error types and their HTTP translation.

Uniform error shape (stable from day one):

    {"error": {"code": "...", "message": "..."}}

Messages never echo field values (`.ai/security.md`).
"""

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.shared.telemetry import TRACE_ID_SCOPE_KEY


class WorkPulseError(Exception):
    """Base class for expected WorkPulse errors.

    Domains subclass this with a specific, stable `code`; the registered handler
    turns it into the uniform error shape. Subclasses that need response
    headers (e.g. `Retry-After`) pass a bounded mapping; the handler merges it
    into the `JSONResponse`.
    """

    status_code = 400
    code = "workpulse_error"
    headers: Mapping[str, str] | None = None

    def __init__(
        self,
        message: str = "Unexpected error",
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message
        self.headers = headers
        super().__init__(message)


class ForbiddenError(WorkPulseError):
    """Policy denial or unknown role — fail closed (403)."""

    status_code = 403
    code = "forbidden"

    def __init__(self, message: str = "Not allowed") -> None:
        super().__init__(message)


class UnauthenticatedError(WorkPulseError):
    """Missing, unknown, expired or revoked session — fail closed (401)."""

    status_code = 401
    code = "unauthenticated"

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message)


class RateLimitedError(WorkPulseError):
    """A rate-limit tier budget was exhausted (429 + `Retry-After`).

    Routers/services may raise it; the limiter middleware, which runs outside
    Starlette's `ExceptionMiddleware`, must catch it and return
    :func:`error_response` instead of letting it propagate (a raised middleware
    exception would become a generic 500). A throttled request mutates nothing.
    `retry_after` is whole seconds until the window resets; it is clamped to at
    least one second so a client never receives a zero/negative delay.
    """

    status_code = 429
    code = "rate_limited"

    def __init__(
        self,
        retry_after: int,
        message: str = "Too many requests; retry after the indicated delay",
    ) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(
            message,
            headers={"Retry-After": str(self.retry_after)},
        )


class DependencyUnavailableError(WorkPulseError):
    """A wrapped dependency is unavailable — fail fast, never hang (503).

    The circuit breaker raises its `CircuitOpenError` subclass when a breaker
    is open; callers see the same stable `dependency_unavailable` code as any
    other unavailable-dependency condition.
    """

    status_code = 503
    code = "dependency_unavailable"

    def __init__(
        self, message: str = "A required service is temporarily unavailable"
    ) -> None:
        super().__init__(message)


def error_response(exc: WorkPulseError) -> JSONResponse:
    """Render an expected error in the house shape.

    Shared by the exception handlers and by user middleware: Starlette runs
    middleware outside `ExceptionMiddleware`, so a middleware that short-
    circuits (the rate limiter) returns this response itself instead of raising
    an exception the handlers cannot see.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
    )


def register_error_handlers(application: FastAPI) -> None:
    """Attach handlers that keep every error response the same shape."""

    @application.exception_handler(WorkPulseError)
    async def _handle_workpulse_error(_: Request, exc: WorkPulseError) -> JSONResponse:
        return error_response(exc)

    @application.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        # Generic message on purpose: never echo submitted values back.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                }
            },
        )

    @application.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, __: Exception) -> JSONResponse:
        # Uniform 500 shape, no exception detail (`.ai/security.md`). Starlette
        # renders this response outside the user middleware stack, so the
        # telemetry middleware hands the trace id over on the request scope
        # (ADR 0023); debug tracebacks bypass exception handlers by design.
        response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                }
            },
        )
        trace_id = request.scope.get(TRACE_ID_SCOPE_KEY)
        if isinstance(trace_id, str) and trace_id:
            response.headers["X-Trace-Id"] = trace_id
        return response
