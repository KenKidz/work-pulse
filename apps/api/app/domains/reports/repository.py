"""Domain-owned I/O boundaries for Analytics & Reports (T4/T5, ADR 0019/0024).

`ReportsRepository` is the read-only aggregate boundary the API service uses.
Its adapter gates every query on the rebuild fence and applies `tenant_id`
before anything else (ADR 0019 decisions 7 and 10). The projection itself is
written by the CDC sink (`workers/cdc_sink`), not this package: the domain
owns the report shapes, policy and metric math, never the pipeline.
"""

from collections.abc import Sequence
from typing import Protocol

from app.domains.reports.schemas import (
    AttendanceAggregateRow,
    HeadcountAggregateRow,
    LeaveAggregateRow,
    OvertimeAggregateRow,
    ReportQuery,
)


class ReportsRepository(Protocol):
    """Read-only aggregate boundary backing the four reports (T5 adapter)."""

    def ensure_ready(self) -> None:
        """Fail with `ReportsUnavailableError` while a rebuild is in progress.

        A missing `projection_state` row is a fresh projection and is served
        normally; `ready = 0` is the rebuild fence (ADR 0019 decision 7).
        """
        ...

    def overtime_aggregates(self, query: ReportQuery) -> Sequence[OvertimeAggregateRow]:
        """Verified overtime aggregates grouped by bucket and dimensions."""
        ...

    def attendance_aggregates(
        self, query: ReportQuery
    ) -> Sequence[AttendanceAggregateRow]:
        """Attendance aggregates grouped by bucket and dimensions."""
        ...

    def leave_aggregates(self, query: ReportQuery) -> Sequence[LeaveAggregateRow]:
        """Approved leave aggregates grouped by bucket and dimensions."""
        ...

    def headcount_aggregates(
        self, query: ReportQuery
    ) -> Sequence[HeadcountAggregateRow]:
        """Headcount/joiner/leaver aggregates from the dimension tables."""
        ...


__all__ = ["ReportsRepository"]
