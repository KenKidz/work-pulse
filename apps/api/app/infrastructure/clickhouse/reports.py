"""ClickHouse implementation of the reports read boundary (T5, ADR 0019).

Every query is read-only, tenant-scoped first, and gated on the rebuild fence
(`projection_state.ready = 0` → `503 reports_unavailable`). Fact reports are a
single `GROUP BY` over the current-state tables with `FINAL`, so a forgotten
dedupe surfaces as doubled aggregates in tests rather than silent drift
(ADR 0019 decisions 2/4/7). Bucket boundaries are pure date expressions:

- `day` → the work date; `week` → Monday (`toStartOfWeek(…, 1)`);
- `month` → the month start; `total` → one `bucket = null` group.

Attribution is query-time as-of (CDC decision D3): facts carry no live
dimension stamps, so every fact row resolves its assignment interval — and its
tenure anchor from `dim_employee` — at read time, exactly as
`folds.resolve_assignment` does (latest interval starting on or before the fact
date, earliest-interval fallback, `unknown` when the employee has no
intervals). A late/backdated `dim_assignment` row therefore corrects
attribution on the next query with no re-stamp pass, and a fact with no
dimension history is never dropped.

Seniority is resolved per fact date in SQL (tenure measured at the bucket end,
clipped to `to`) with the same anniversary thresholds as the fold's
`seniority_band`; a pre-extension fact (null `hired_at`) groups as `unknown`.

Headcount is folded in Python from the two dimension tables: it needs a
covering-assignment lookup per bucket that is clearer (and easier to keep
identical to the fold) than a multi-way ClickHouse join. The volume is
employees, not events; every fact-table query stays in the store.

Any client failure (unreachable store, timeout, broken query) becomes
`ReportsUnavailableError`; the API answers the house `503` shape and the web
renders its error state. The write path never depends on this module.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID

from clickhouse_connect.driver.client import Client

from app.domains.reports.exceptions import ReportsUnavailableError
from app.domains.reports.folds import resolve_ordered_assignment, seniority_band
from app.domains.reports.schemas import (
    UNKNOWN_GROUP_KEY,
    AssignmentRow,
    AttendanceAggregateRow,
    EmployeeRow,
    HeadcountAggregateRow,
    LeaveAggregateRow,
    OvertimeAggregateRow,
    ReportQuery,
    add_months,
)

_HUNDREDTH = Decimal("0.01")

#: Trusted date column per fact table (never user input).
_OVERTIME_DATE = "work_date"
_ATTENDANCE_DATE = "workday"
_LEAVE_DATE = "start_date"

_UUID_KEY_COLUMNS = {
    "department": "department_id",
    "location": "location_id",
    "position": "position_id",
}


def _decimal(value: object) -> Decimal:
    """Parse a ClickHouse decimal without going through binary float."""
    return Decimal(str(value)).quantize(_HUNDREDTH, rounding=ROUND_HALF_EVEN)


class ClickhouseReportsRepository:
    """`ReportsRepository` over the ADR 0019 ClickHouse projection."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._checked_ready = False

    # --- Rebuild fence -------------------------------------------------

    def ensure_ready(self) -> None:
        """Serve a fresh projection; refuse an in-progress rebuild.

        A missing `reports` marker means the projection has never been fenced
        (a first start), so it is served normally (ADR 0019 decision 7).
        """
        if self._checked_ready:
            return
        rows = self._query(
            "SELECT ready FROM projection_state FINAL WHERE key = 'reports'", {}
        )
        if rows and int(rows[0][0]) == 0:
            raise ReportsUnavailableError("Reports are being rebuilt")
        self._checked_ready = True

    # --- Fact aggregates ----------------------------------------------

    def overtime_aggregates(self, query: ReportQuery) -> Sequence[OvertimeAggregateRow]:
        if self._empty_scope(query):
            return []
        self.ensure_ready()
        sql, parameters = self._fact_query(
            query,
            table="fact_overtime",
            date_column=_OVERTIME_DATE,
            metrics=(
                "sum(hours) AS verified_hours, "
                "count() AS verified_request_count, "
                "uniqExact(work_date) AS verified_day_count"
            ),
            extra_conditions=("status = 'hr_verified'",),
        )
        rows = self._query(sql, parameters)
        if not rows:
            # No fact rows → no report rows → no denominator query.
            return []
        width = len(query.group_by)
        # The denominator is the people employed in the group, not the subset
        # with a claim (PO Decision 6 / plan R4).
        active = self._active_employee_counts(query)
        aggregates: list[OvertimeAggregateRow] = []
        for row in rows:
            keys = _keys(row[1 : 1 + width])
            aggregates.append(
                OvertimeAggregateRow(
                    bucket=row[0],
                    keys=keys,
                    verified_hours=_decimal(row[1 + width]),
                    verified_request_count=int(row[2 + width]),
                    verified_day_count=int(row[3 + width]),
                    active_employee_count=active.get((row[0], keys), 0),
                )
            )
        return aggregates

    def _active_employee_counts(
        self, query: ReportQuery
    ) -> dict[tuple[date | None, tuple[str, ...]], int]:
        """Employees whose employment overlaps each bucket, per group row.

        This is the overtime "rate" denominator (PO Decision 6): the report
        normalizes verified hours by the people employed in the group during
        the bucket, **not** by the subset who happened to claim overtime. A
        transfer can put one employee in two groups inside one bucket — their
        facts split at `effective_from`, and so does the denominator.

        The fold is a ClickHouse aggregate over a generated bucket spine, not a
        Python scan of the tenant: the flagship query must stay inside the
        Phase-4 budget as the workforce grows, and only the groups that already
        have verified overtime are consumed by the caller.
        """
        if query.employee_scope is not None and not query.employee_scope:
            return {}

        buckets = _buckets(query.start, query.end, query.interval)
        if not buckets:
            return {}

        # Bucket dates are derived from the validated query range, never raw
        # request text; interpolating them keeps the `arrayJoin` spine simple.
        # `total` has no bucket label — a null first tuple element matches the
        # fact report's `bucket: null`.
        bucket_values = ", ".join(
            f"({_bucket_label_sql(bucket)}, "
            f"toDate('{coverage_start.isoformat()}'), "
            f"toDate('{coverage_end.isoformat()}'))"
            for bucket, coverage_start, coverage_end in buckets
        )

        parameters: dict[str, Any] = {"tenant_id": query.tenant_id}
        scope_condition = ""
        if query.employee_scope is not None:
            scope_condition = " AND employee_id IN {scope:Array(UUID)}"
            parameters["scope"] = list(query.employee_scope)
        filter_conditions: list[str] = []
        self._add_filters(query, filter_conditions, parameters)
        filter_sql = "".join(f" AND {condition}" for condition in filter_conditions)

        keys = [
            _denominator_group_expression(dimension) for dimension in query.group_by
        ]
        selected_keys = ", ".join(
            f"{expression} AS k{index}" for index, expression in enumerate(keys)
        )
        ordinals = ", ".join(str(position) for position in range(1, 2 + len(keys)))
        sql = f"""
WITH buckets AS (
    SELECT
        tupleElement(b, 1) AS bucket,
        tupleElement(b, 2) AS coverage_start,
        tupleElement(b, 3) AS coverage_end
    FROM (SELECT arrayJoin([{bucket_values}]) AS b)
),
employees AS (
    SELECT tenant_id, employee_id, hired_at, terminated_at
    FROM dim_employee FINAL
    WHERE tenant_id = {{tenant_id:UUID}}{scope_condition}
),
first_assignment AS (
    SELECT tenant_id, employee_id, min(effective_from) AS first_start
    FROM dim_assignment FINAL
    WHERE tenant_id = {{tenant_id:UUID}}{scope_condition}
    GROUP BY tenant_id, employee_id
),
assignments AS (
    SELECT tenant_id, employee_id, effective_from, effective_to,
           department_id, position_id, location_id
    FROM dim_assignment FINAL
    WHERE tenant_id = {{tenant_id:UUID}}{scope_condition}{filter_sql}
)
SELECT
    b.bucket AS bucket,
    {selected_keys},
    uniqExact(e.employee_id) AS active_employee_count
FROM buckets AS b
INNER JOIN employees AS e ON 1 = 1
INNER JOIN first_assignment AS fa
    ON fa.tenant_id = e.tenant_id AND fa.employee_id = e.employee_id
INNER JOIN assignments AS a
    ON a.tenant_id = e.tenant_id AND a.employee_id = e.employee_id
WHERE ifNull(e.hired_at, fa.first_start) <= b.coverage_end
  AND (e.terminated_at IS NULL OR e.terminated_at >= b.coverage_start)
  AND (
      (
          a.effective_from <= b.coverage_end
          AND (a.effective_to IS NULL OR a.effective_to > b.coverage_start)
      )
      OR (
          a.effective_to = b.coverage_start
          AND e.terminated_at = b.coverage_start
      )
  )
GROUP BY {ordinals}
ORDER BY {ordinals}
"""
        rows = self._query(sql, parameters)
        width = len(query.group_by)
        return {
            (row[0], _keys(row[1 : 1 + width])): int(row[1 + width]) for row in rows
        }

    def attendance_aggregates(
        self, query: ReportQuery
    ) -> Sequence[AttendanceAggregateRow]:
        if self._empty_scope(query):
            return []
        self.ensure_ready()
        sql, parameters = self._fact_query(
            query,
            table="fact_attendance",
            date_column=_ATTENDANCE_DATE,
            metrics=(
                "count() AS checkin_count, "
                "countIf(duration_minutes IS NOT NULL) AS worked_day_count, "
                "sum(ifNull(duration_minutes, 0)) AS worked_minutes, "
                "countIf(late) AS late_count, "
                "sumIf(ifNull(minutes_late, 0), late) AS minutes_late_total"
            ),
        )
        rows = self._query(sql, parameters)
        width = len(query.group_by)
        return [
            AttendanceAggregateRow(
                bucket=row[0],
                keys=_keys(row[1 : 1 + width]),
                checkin_count=int(row[1 + width]),
                worked_day_count=int(row[2 + width]),
                worked_minutes=int(row[3 + width]),
                late_count=int(row[4 + width]),
                minutes_late_total=int(row[5 + width]),
            )
            for row in rows
        ]

    def leave_aggregates(self, query: ReportQuery) -> Sequence[LeaveAggregateRow]:
        if self._empty_scope(query):
            return []
        self.ensure_ready()
        sql, parameters = self._fact_query(
            query,
            table="fact_leave",
            date_column=_LEAVE_DATE,
            metrics=(
                "sum(approved_day_count) AS approved_day_count, "
                "count() AS approved_request_count"
            ),
        )
        rows = self._query(sql, parameters)
        width = len(query.group_by)
        return [
            LeaveAggregateRow(
                bucket=row[0],
                keys=_keys(row[1 : 1 + width]),
                approved_day_count=int(row[1 + width]),
                approved_request_count=int(row[2 + width]),
            )
            for row in rows
        ]

    # --- Headcount -----------------------------------------------------

    def headcount_aggregates(
        self, query: ReportQuery
    ) -> Sequence[HeadcountAggregateRow]:
        """Headcount/joiners/leavers from the folded dimension state.

        - active at the bucket's as-of date: hired on or before it and not
          terminated before it;
        - joiners: hire anchor in the clipped bucket;
        - leavers: `terminated_at` in the clipped bucket;
        - the grouping assignment is the one covering the as-of date, so a
          transfer moves the employee between groups at `effective_from`;
          joiners and leavers share that attribution (one employee, one row
          per bucket);
        - a pre-extension row (null `hired_at`) uses its first assignment start
          as the hire anchor, but still groups as `unknown` seniority.
        """
        if self._empty_scope(query):
            return []
        self.ensure_ready()

        employees = self._employees(query)
        assignments = self._assignments(query)
        by_employee: dict[UUID, list[AssignmentRow]] = {}
        for assignment in assignments:
            by_employee.setdefault(assignment.employee_id, []).append(assignment)
        # Sort once per employee, not once per bucket visit.
        for employee_assignments in by_employee.values():
            employee_assignments.sort(key=lambda assignment: assignment.effective_from)

        counters: dict[tuple[date | None, tuple[str, ...]], list[int]] = {}
        for bucket, coverage_start, coverage_end in _buckets(
            query.start, query.end, query.interval
        ):
            as_of = coverage_end
            for employee in employees:
                employee_assignments = by_employee.get(employee.employee_id, [])
                first_start = (
                    employee_assignments[0].effective_from
                    if employee_assignments
                    else None
                )
                anchor = employee.hired_at or first_start
                assignment = resolve_ordered_assignment(employee_assignments, as_of)

                if not _matches_filters(query, assignment):
                    continue

                active = (
                    anchor is not None
                    and anchor <= as_of
                    and (
                        employee.terminated_at is None
                        or employee.terminated_at >= as_of
                    )
                )
                joiner = anchor is not None and coverage_start <= anchor <= coverage_end
                leaver = (
                    employee.terminated_at is not None
                    and coverage_start <= employee.terminated_at <= coverage_end
                )
                if not (active or joiner or leaver):
                    continue

                keys = tuple(
                    _dimension_key(dimension, employee, assignment, as_of)
                    for dimension in query.group_by
                )
                row = counters.setdefault((bucket, keys), [0, 0, 0])
                row[0] += int(active)
                row[1] += int(joiner)
                row[2] += int(leaver)

        return [
            HeadcountAggregateRow(
                bucket=bucket,
                keys=keys,
                headcount=counts[0],
                joiners=counts[1],
                leavers=counts[2],
            )
            for (bucket, keys), counts in sorted(
                counters.items(),
                key=lambda item: (
                    item[0][0] or date.min,
                    item[0][1],
                ),
            )
        ]

    def _employees(self, query: ReportQuery) -> list[EmployeeRow]:
        conditions = ["tenant_id = {tenant_id:UUID}"]
        parameters: dict[str, Any] = {"tenant_id": query.tenant_id}
        self._add_scope(query, conditions, parameters)
        rows = self._query(
            "SELECT employee_id, hired_at, terminated_at FROM dim_employee FINAL "
            "WHERE " + " AND ".join(conditions),
            parameters,
        )
        return [
            EmployeeRow(
                tenant_id=query.tenant_id,
                employee_id=row[0],
                hired_at=row[1],
                terminated_at=row[2],
            )
            for row in rows
        ]

    def _assignments(self, query: ReportQuery) -> list[AssignmentRow]:
        conditions = ["tenant_id = {tenant_id:UUID}"]
        parameters: dict[str, Any] = {"tenant_id": query.tenant_id}
        self._add_scope(query, conditions, parameters)
        rows = self._query(
            "SELECT employee_id, effective_from, effective_to, department_id, "
            "position_id, location_id FROM dim_assignment FINAL "
            "WHERE " + " AND ".join(conditions) + " ORDER BY effective_from",
            parameters,
        )
        return [
            AssignmentRow(
                tenant_id=query.tenant_id,
                employee_id=row[0],
                effective_from=row[1],
                effective_to=row[2],
                department_id=row[3],
                position_id=row[4],
                location_id=row[5],
            )
            for row in rows
        ]

    # --- SQL helpers ---------------------------------------------------

    def _fact_query(
        self,
        query: ReportQuery,
        *,
        table: str,
        date_column: str,
        metrics: str,
        extra_conditions: Sequence[str] = (),
    ) -> tuple[str, dict[str, Any]]:
        """One grouped aggregate with query-time as-of attribution (D3).

        The fact table joins the `dim_assignment` lookup with `ASOF LEFT JOIN`
        on the fact's business date. The lookup adds a synthetic pre-history row
        (`effective_from = 1970-01-01`) per employee carrying the earliest
        interval's dimensions, so a fact predating the first known interval
        still resolves to that interval — the same fallback as
        `folds.resolve_assignment`; an employee with no intervals matches
        nothing and groups as `unknown` rather than disappearing.
        """
        parameters: dict[str, Any] = {
            "tenant_id": query.tenant_id,
            "start": query.start,
            "end": query.end,
        }
        date_expression = f"f.{date_column}"
        conditions = [
            "f.tenant_id = {tenant_id:UUID}",
            f"{date_expression} >= {{start:Date}}",
            f"{date_expression} <= {{end:Date}}",
            *extra_conditions,
        ]
        self._add_filters(query, conditions, parameters, prefix="a.")
        self._add_scope(query, conditions, parameters, prefix="f.")
        scope_condition = ""
        if query.employee_scope is not None:
            scope_condition = " AND employee_id IN {scope:Array(UUID)}"

        if query.interval == "total":
            selected = ["CAST(NULL, 'Nullable(Date)') AS bucket"]
        else:
            selected = [
                f"{_bucket_expression(date_expression, query.interval)} AS bucket"
            ]
        for index, dimension in enumerate(query.group_by):
            expression = _group_expression(dimension, date_expression, query.interval)
            selected.append(f"{expression} AS k{index}")
        selected.append(metrics)

        ordinals = ", ".join(
            str(position) for position in range(1, 2 + len(query.group_by))
        )
        sql = f"""
WITH assignments AS (
    SELECT tenant_id, employee_id, effective_from,
           tuple(department_id, position_id, location_id) AS dimensions
    FROM dim_assignment FINAL
    WHERE tenant_id = {{tenant_id:UUID}}{scope_condition}
),
first_assignment AS (
    SELECT tenant_id, employee_id,
           argMin(dimensions, effective_from) AS dimensions
    FROM assignments
    GROUP BY tenant_id, employee_id
),
assignment_lookup AS (
    SELECT tenant_id, employee_id, effective_from,
           tupleElement(dimensions, 1) AS department_id,
           tupleElement(dimensions, 2) AS position_id,
           tupleElement(dimensions, 3) AS location_id
    FROM assignments
    UNION ALL
    SELECT tenant_id, employee_id, toDate('1970-01-01') AS effective_from,
           tupleElement(dimensions, 1) AS department_id,
           tupleElement(dimensions, 2) AS position_id,
           tupleElement(dimensions, 3) AS location_id
    FROM first_assignment
)
SELECT {", ".join(selected)}
FROM {table} AS f FINAL
ASOF LEFT JOIN assignment_lookup AS a
    ON a.tenant_id = f.tenant_id
   AND a.employee_id = f.employee_id
   AND {date_expression} >= a.effective_from
LEFT JOIN dim_employee AS e FINAL
    ON e.tenant_id = f.tenant_id
   AND e.employee_id = f.employee_id
WHERE {" AND ".join(conditions)}
GROUP BY {ordinals}
ORDER BY {ordinals}
"""
        return sql, parameters

    @staticmethod
    def _add_filters(
        query: ReportQuery,
        conditions: list[str],
        parameters: dict[str, Any],
        *,
        prefix: str = "",
    ) -> None:
        for name, value in (
            ("department_id", query.department_id),
            ("location_id", query.location_id),
            ("position_id", query.position_id),
        ):
            if value is not None:
                conditions.append(f"{prefix}{name} = {{{name}:UUID}}")
                parameters[name] = value

    @staticmethod
    def _add_scope(
        query: ReportQuery,
        conditions: list[str],
        parameters: dict[str, Any],
        *,
        prefix: str = "",
    ) -> None:
        if query.employee_scope is not None:
            conditions.append(f"{prefix}employee_id IN {{scope:Array(UUID)}}")
            parameters["scope"] = list(query.employee_scope)

    @staticmethod
    def _empty_scope(query: ReportQuery) -> bool:
        return query.employee_scope is not None and not query.employee_scope

    def _query(self, sql: str, parameters: dict[str, Any]) -> Sequence[Sequence[Any]]:
        try:
            return self._client.query(sql, parameters=parameters).result_rows
        except Exception as exc:  # noqa: BLE001 - every store failure is a 503
            raise ReportsUnavailableError() from exc


# --- Pure SQL/date helpers -------------------------------------------------


def _keys(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _bucket_expression(date_column: str, interval: str) -> str:
    if interval == "day":
        return f"toDate({date_column})"
    if interval == "week":
        # Monday-start weeks (`mode = 1`).
        return f"toStartOfWeek({date_column}, 1)"
    return f"toStartOfMonth({date_column})"


def _group_expression(dimension: str, date_column: str, interval: str) -> str:
    """The SQL group key; dims resolve through the as-of join (`a`), tenure
    through the employee dimension (`e`), leave type straight off the fact."""
    if dimension in _UUID_KEY_COLUMNS:
        column = _UUID_KEY_COLUMNS[dimension]
        return f"ifNull(toString(a.{column}), '{UNKNOWN_GROUP_KEY}')"
    if dimension == "seniority":
        return _seniority_expression("e.hired_at", date_column, interval)
    # The leave report's fifth dimension.
    return "f.leave_type_code"


#: Tenure thresholds shared by every SQL seniority band (the Python twin lives
#: in `folds.seniority_band`; `test_reports_api.py` pins parity at boundaries).
_SENIORITY_THRESHOLDS = ((5, "5y_plus"), (3, "3_5y"), (1, "1_3y"))


def _seniority_band_expression(hired_at: str, tenure_at: str) -> str:
    """The SQL band expression for a hired-at column and a tenure date."""
    branches = " ".join(
        f"{tenure_at} >= addYears({hired_at}, {years}), '{band}',"
        for years, band in _SENIORITY_THRESHOLDS
    )
    return f"multiIf({hired_at} IS NULL, 'unknown', {branches} 'lt_1y')"


def _denominator_group_expression(dimension: str) -> str:
    """Group key for the denominator query (aliases `a`, `e`, `b`)."""
    if dimension in _UUID_KEY_COLUMNS:
        column = _UUID_KEY_COLUMNS[dimension]
        return f"ifNull(toString(a.{column}), '{UNKNOWN_GROUP_KEY}')"
    if dimension == "seniority":
        return _seniority_band_expression("e.hired_at", "b.coverage_end")
    # `leave_type` is leave-only and never reaches the overtime denominator.
    return f"'{UNKNOWN_GROUP_KEY}'"


def _seniority_expression(hired_column: str, date_column: str, interval: str) -> str:
    """SQL twin of `folds.seniority_band` at the row's bucket end."""
    if interval == "total":
        tenure_at = "{end:Date}"
    elif interval == "day":
        tenure_at = date_column
    elif interval == "week":
        tenure_at = f"least(addDays(toStartOfWeek({date_column}, 1), 6), {{end:Date}})"
    else:
        tenure_at = f"least(toLastDayOfMonth({date_column}), {{end:Date}})"
    return _seniority_band_expression(hired_column, tenure_at)


def _bucket_label_sql(bucket: date | None) -> str:
    if bucket is None:
        return "CAST(NULL, 'Nullable(Date)')"
    return f"toDate('{bucket.isoformat()}')"


def _buckets(
    start: date, end: date, interval: str
) -> list[tuple[date | None, date, date]]:
    """`(bucket label, coverage start, coverage end)` for the inclusive range.

    Buckets cover `[start, end]`; the first/last are clipped to the range while
    the bucket label stays the calendar period start (a mid-month `from` starts
    that month's bucket mid-way).
    """
    if interval == "total":
        return [(None, start, end)]
    if interval == "day":
        return [(cursor, cursor, cursor) for cursor in _days(start, end)]
    if interval == "week":
        buckets: list[tuple[date | None, date, date]] = []
        cursor = start - timedelta(days=start.weekday())
        while cursor <= end:
            buckets.append(
                (
                    cursor,
                    max(cursor, start),
                    min(cursor + timedelta(days=6), end),
                )
            )
            cursor += timedelta(days=7)
        return buckets

    buckets = []
    cursor = start.replace(day=1)
    while cursor <= end:
        period_end = add_months(cursor, 1) - timedelta(days=1)
        buckets.append((cursor, max(cursor, start), min(period_end, end)))
        cursor = add_months(cursor, 1)
    return buckets


def _days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _matches_filters(query: ReportQuery, assignment: AssignmentRow | None) -> bool:
    if query.department_id is not None and (
        assignment is None or assignment.department_id != query.department_id
    ):
        return False
    if query.location_id is not None and (
        assignment is None or assignment.location_id != query.location_id
    ):
        return False
    if query.position_id is not None and (
        assignment is None or assignment.position_id != query.position_id
    ):
        return False
    return True


def _dimension_key(
    dimension: str,
    employee: EmployeeRow,
    assignment: AssignmentRow | None,
    as_of: date,
) -> str:
    if dimension == "seniority":
        return seniority_band(employee.hired_at, as_of)
    column = _UUID_KEY_COLUMNS.get(dimension)
    if column is None or assignment is None:
        return UNKNOWN_GROUP_KEY
    value = getattr(assignment, column)
    return str(value) if value is not None else UNKNOWN_GROUP_KEY


__all__ = ["ClickhouseReportsRepository"]
