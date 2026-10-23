"""Use cases for Analytics & Reports (T5) — read-only query orchestration.

The service is the one place report semantics live:

- policy (`ensure_can_read_reports`) then scope: a manager's query carries the
  self + direct-reports employee set from the read-only port; `None` means
  company-wide and an empty tuple means "no employees" (never company-wide).
- derived metric math (hours per employee, late rate, average minutes late)
  with ADR 0017's half-even rounding; the repository returns only raw counts,
  hours and minutes, so every ratio is computed in exactly one place.
- display labels: identifiers stay in ClickHouse; the port resolves names at
  query time. Seniority bands are local labels (a closed vocabulary).
- no wall-clock reads and no writes: reports are a projection read, never an
  audited mutation.

The ClickHouse repository is synchronous (`clickhouse-connect`'s client is),
so every store call runs in a worker thread via `anyio.to_thread`; the event
loop stays free while the aggregate runs (ADR 0019 decision 12).
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol
from uuid import UUID

from anyio import to_thread

from app.domains.reports.policies import ensure_can_read_reports
from app.domains.reports.ports import EmployeeScope, ReferenceLabels
from app.domains.reports.repository import ReportsRepository
from app.domains.reports.schemas import (
    SENIORITY_BAND_LABELS,
    UNKNOWN_GROUP_KEY,
    AttendanceReport,
    AttendanceReportRow,
    HeadcountReport,
    HeadcountReportRow,
    LeaveReport,
    LeaveReportParams,
    LeaveReportRow,
    OvertimeReport,
    OvertimeReportRow,
    ReportDimension,
    ReportGroup,
    ReportQuery,
    ReportQueryParams,
)
from app.shared.identity import Identity, Role

_UNKNOWN_LABEL = "Unknown"

_HUNDREDTH = Decimal("0.01")
_TEN_THOUSANDTH = Decimal("0.0001")
_TENTH = Decimal("0.1")
_ZERO = Decimal("0")


class _AggregateRow(Protocol):
    """The one field every aggregate row shares (label resolution needs it)."""

    @property
    def keys(self) -> tuple[str, ...]: ...


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _ratio(numerator: int, denominator: int, quantum: Decimal) -> Decimal:
    """A rounded ratio; a zero denominator is `0`, never a division error."""
    if denominator == 0:
        return _ZERO.quantize(quantum)
    return _quantize(Decimal(numerator) / Decimal(denominator), quantum)


def _hours_per_employee(verified_hours: Decimal, active_employee_count: int) -> Decimal:
    """Verified hours / active employees, 2dp half-even; `0.00` without people."""
    if active_employee_count == 0:
        return Decimal("0.00")
    return _quantize(verified_hours / Decimal(active_employee_count), _HUNDREDTH)


class ReportsService:
    """Read-only report queries over the analytics projection."""

    def __init__(
        self,
        *,
        repo: ReportsRepository,
        labels: ReferenceLabels,
        scope: EmployeeScope,
    ) -> None:
        self._repo = repo
        self._labels = labels
        self._scope = scope

    # --- Overtime ------------------------------------------------------

    async def overtime(
        self, identity: Identity, params: ReportQueryParams
    ) -> OvertimeReport:
        query = await self._prepare(identity, params)
        aggregates = await to_thread.run_sync(self._repo.overtime_aggregates, query)
        labels = await self._labels_for(identity.tenant_id, query.group_by, aggregates)

        rows = [
            OvertimeReportRow(
                bucket=aggregate.bucket,
                groups=self._groups(query.group_by, aggregate.keys, labels),
                verified_hours=aggregate.verified_hours,
                verified_request_count=aggregate.verified_request_count,
                verified_day_count=aggregate.verified_day_count,
                active_employee_count=aggregate.active_employee_count,
                hours_per_employee=_hours_per_employee(
                    aggregate.verified_hours, aggregate.active_employee_count
                ),
            )
            for aggregate in aggregates
        ]
        return OvertimeReport.model_validate(
            {
                "from": params.start,
                "to": params.end,
                "group_by": [ReportDimension(d) for d in query.group_by],
                "interval": params.interval,
                "rows": rows,
            }
        )

    # --- Attendance ----------------------------------------------------

    async def attendance(
        self, identity: Identity, params: ReportQueryParams
    ) -> AttendanceReport:
        query = await self._prepare(identity, params)
        aggregates = await to_thread.run_sync(self._repo.attendance_aggregates, query)
        labels = await self._labels_for(identity.tenant_id, query.group_by, aggregates)

        rows = [
            AttendanceReportRow(
                bucket=aggregate.bucket,
                groups=self._groups(query.group_by, aggregate.keys, labels),
                checkin_count=aggregate.checkin_count,
                worked_day_count=aggregate.worked_day_count,
                worked_minutes=aggregate.worked_minutes,
                late_count=aggregate.late_count,
                late_rate=_ratio(
                    aggregate.late_count, aggregate.checkin_count, _TEN_THOUSANDTH
                ),
                avg_minutes_late=_ratio(
                    aggregate.minutes_late_total, aggregate.late_count, _TENTH
                ),
            )
            for aggregate in aggregates
        ]
        return AttendanceReport.model_validate(
            {
                "from": params.start,
                "to": params.end,
                "group_by": [ReportDimension(d) for d in query.group_by],
                "interval": params.interval,
                "rows": rows,
            }
        )

    # --- Leave ---------------------------------------------------------

    async def leave(self, identity: Identity, params: LeaveReportParams) -> LeaveReport:
        query = await self._prepare(identity, params)
        aggregates = await to_thread.run_sync(self._repo.leave_aggregates, query)
        labels = await self._labels_for(identity.tenant_id, query.group_by, aggregates)

        rows = [
            LeaveReportRow(
                bucket=aggregate.bucket,
                groups=self._groups(query.group_by, aggregate.keys, labels),
                approved_day_count=aggregate.approved_day_count,
                approved_request_count=aggregate.approved_request_count,
            )
            for aggregate in aggregates
        ]
        return LeaveReport.model_validate(
            {
                "from": params.start,
                "to": params.end,
                "group_by": [ReportDimension(d) for d in query.group_by],
                "interval": params.interval,
                "rows": rows,
            }
        )

    # --- Headcount -----------------------------------------------------

    async def headcount(
        self, identity: Identity, params: ReportQueryParams
    ) -> HeadcountReport:
        query = await self._prepare(identity, params)
        aggregates = await to_thread.run_sync(self._repo.headcount_aggregates, query)
        labels = await self._labels_for(identity.tenant_id, query.group_by, aggregates)

        rows = [
            HeadcountReportRow(
                bucket=aggregate.bucket,
                groups=self._groups(query.group_by, aggregate.keys, labels),
                headcount=aggregate.headcount,
                joiners=aggregate.joiners,
                leavers=aggregate.leavers,
            )
            for aggregate in aggregates
        ]
        return HeadcountReport.model_validate(
            {
                "from": params.start,
                "to": params.end,
                "group_by": [ReportDimension(d) for d in query.group_by],
                "interval": params.interval,
                "rows": rows,
            }
        )

    # --- Shared query preparation -------------------------------------

    async def _prepare(
        self, identity: Identity, params: ReportQueryParams
    ) -> ReportQuery:
        """Policy → manager scope → validated `ReportQuery` (no store call yet)."""
        ensure_can_read_reports(identity)

        employee_scope: tuple[UUID, ...] | None = None
        if identity.role == Role.MANAGER:
            visible = await self._scope.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
            employee_scope = tuple(visible)

        query = ReportQuery(
            tenant_id=identity.tenant_id,
            start=params.start,
            end=params.end,
            interval=params.interval.value,
            group_by=params.dimensions,
            department_id=params.department_id,
            location_id=params.location_id,
            position_id=params.position_id,
            employee_scope=employee_scope,
        )
        # The adapter's rebuild-fence read lives here, once per request, and
        # keeps the fence check off the event loop (it is a store read).
        await to_thread.run_sync(self._repo.ensure_ready)
        return query

    # --- Labels --------------------------------------------------------

    async def _labels_for(
        self,
        tenant_id: UUID,
        dimensions: Sequence[str],
        aggregates: Iterable[_AggregateRow],
    ) -> Mapping[tuple[str, str], str]:
        """Resolve every non-seniority group key through the read-only port."""
        wanted: dict[str, set[str]] = {}
        for aggregate in aggregates:
            for dimension, key in zip(dimensions, aggregate.keys, strict=True):
                if dimension == "seniority" or key == UNKNOWN_GROUP_KEY:
                    continue
                wanted.setdefault(dimension, set()).add(key)

        resolved: dict[tuple[str, str], str] = {}
        for dimension, keys in wanted.items():
            labels = await self._labels.labels(
                tenant_id, dimension, tuple(sorted(keys))
            )
            for key, label in labels.items():
                resolved[(dimension, key)] = label
        return resolved

    def _groups(
        self,
        dimensions: Sequence[str],
        keys: Sequence[str],
        labels: Mapping[tuple[str, str], str],
    ) -> list[ReportGroup]:
        groups: list[ReportGroup] = []
        for dimension, key in zip(dimensions, keys, strict=True):
            if dimension == "seniority":
                label = SENIORITY_BAND_LABELS.get(key, _UNKNOWN_LABEL)
            elif key == UNKNOWN_GROUP_KEY:
                label = _UNKNOWN_LABEL
            else:
                label = labels.get((dimension, key), _UNKNOWN_LABEL)
            groups.append(
                ReportGroup(
                    dimension=ReportDimension(dimension),
                    key=key,
                    label=label,
                )
            )
        return groups


__all__ = ["ReportsService"]
