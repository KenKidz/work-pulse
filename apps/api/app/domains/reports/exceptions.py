"""Analytics & Reports domain errors.

The reports surface is read-only but depends on an optional analytical store.
When ClickHouse is unreachable, or the rebuild fence marks the projection as
in progress, the API answers the house error shape with `503` and the stable
code `reports_unavailable` — never a partial rebuild (ADR 0019 decisions 7 and
11). The web renders the same code as its error state. Messages are static and
never echo query values or store detail.
"""

from app.shared.errors import WorkPulseError


class ReportsError(WorkPulseError):
    """Base class for expected report errors."""

    status_code = 500
    code = "reports_error"


class ReportsUnavailableError(ReportsError):
    """The analytics store is unreachable or a rebuild is in progress.

    Raised by the reports repository (its adapter on client failure, and on
    `projection_state.ready = 0`). OLTP writes are unaffected: this is a
    read-side degradation only.
    """

    status_code = 503
    code = "reports_unavailable"

    def __init__(self, message: str = "Reports are temporarily unavailable") -> None:
        super().__init__(message)


__all__ = ["ReportsError", "ReportsUnavailableError"]
