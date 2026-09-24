"""Cross-domain error types and their HTTP translation."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class WorkPulseError(Exception):
    """Base class for expected WorkPulse errors.

    Domains subclass this with a specific, stable `code`; the registered handler
    turns it into the uniform error shape:

        {"error": {"code": "...", "message": "..."}}
    """

    status_code = 400
    code = "workpulse_error"

    def __init__(self, message: str = "Unexpected error") -> None:
        self.message = message
        super().__init__(message)


def register_error_handlers(application: FastAPI) -> None:
    """Attach handlers that keep every error response the same shape."""

    @application.exception_handler(WorkPulseError)
    async def _handle_workpulse_error(_: Request, exc: WorkPulseError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )