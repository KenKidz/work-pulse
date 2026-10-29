"""Cross-domain error types and their HTTP translation.

Uniform error shape (stable from day one):

    {"error": {"code": "...", "message": "..."}}

Messages never echo field values (`.ai/security.md`).
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.shared.telemetry import TRACE_ID_SCOPE_KEY


class WorkPulseError(Exception):
    """Base class for expected WorkPulse errors.

    Domains subclass this with a specific, stable `code`; the registered handler
    turns it into the uniform error shape.
    """

    status_code = 400
    code = "workpulse_error"

    def __init__(self, message: str = "Unexpected error") -> None:
        self.message = message
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


def register_error_handlers(application: FastAPI) -> None:
    """Attach handlers that keep every error response the same shape."""

    @application.exception_handler(WorkPulseError)
    async def _handle_workpulse_error(_: Request, exc: WorkPulseError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

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
