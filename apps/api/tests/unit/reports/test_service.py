"""T5 — report query orchestration and metric math (pure unit tests).

Pinned from `.ai/features/analytics-reports.md` business rules 9–12 and the
acceptance criteria:

- derived ratios round half-even at their declared scale and zero
  denominators are `0`, never a division error;
- policy denies Employee-role callers, and a manager query carries exactly the
  self + direct-reports scope (empty for an unlinked manager, never None);
- labels resolve through the read-only port, with `Unknown` for missing keys;
- `group_by` validation rejects duplicates, unknown dimensions, the leave-only
  dimension on other reports, and ranges over 24 months.

No ClickHouse or Postgres is involved: the repository and both ports are
structural fakes.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.reports.exceptions import ReportsUnavailableError
from app.domains.reports.schemas import (
    AttendanceAggregateRow,
    LeaveAggregateRow,
    LeaveReportParams,
    OvertimeAggregateRow,
    ReportInterval,
    ReportQueryParams,
)
from app.domains.reports.service import ReportsService
from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
USER = UUID("00000000-0000-4000-8000-000000000031")
MANAGER = UUID("00000000-0000-4000-8000-000000000001")
REPORT = UUID("00000000-0000-4000-8000-000000000002")
DEPARTMENT = UUID("00000000-0000-4000-8000-0000000000d1")
LOCATION = UUID("00000000-0000-4000-8000-0000000000f1")


class FakeRepository:
    """Structural `ReportsRepository` fake recording the last query."""

    def __init__(
        self,
        *,
        overtime: tuple[OvertimeAggregateRow, ...] = (),
        attendance: tuple[AttendanceAggregateRow, ...] = (),
        leave: tuple[LeaveAggregateRow, ...] = (),
        headcount: tuple[Any, ...] = (),
        unavailable: bool = False,
    ) -> None:
        self._overtime = overtime
        self._attendance = attendance
        self._leave = leave
        self._headcount = headcount
        self._unavailable = unavailable
        self.last_query: Any = None
        self.ensure_ready_calls = 0

    def ensure_ready(self) -> None:
        self.ensure_ready_calls += 1
        if self._unavailable:
            raise ReportsUnavailableError()

    def overtime_aggregates(self, query: Any) -> tuple[OvertimeAggregateRow, ...]:
        self.last_query = query
        return self._overtime

    def attendance_aggregates(self, query: Any) -> tuple[AttendanceAggregateRow, ...]:
        self.last_query = query
        return self._attendance

    def leave_aggregates(self, query: Any) -> tuple[LeaveAggregateRow, ...]:
        self.last_query = query
        return self._leave

    def headcount_aggregates(self, query: Any) -> tuple[Any, ...]:
        self.last_query = query
        return self._headcount


class FakeLabels:
    def __init__(self, labels: dict[tuple[str, str], str]) -> None:
        self._labels = labels
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def labels(
        self, tenant_id: UUID, dimension: str, keys: Sequence[str]
    ) -> dict[str, str]:
        self.calls.append((dimension, tuple(keys)))
        return {
            key: label
            for (label_dimension, key), label in self._labels.items()
            if label_dimension == dimension and key in keys
        }


class FakeScope:
    def __init__(self, ids: tuple[UUID, ...]) -> None:
        self._ids = ids
        self.calls: list[UUID | None] = []

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> tuple[UUID, ...]:
        self.calls.append(manager_employee_id)
        return self._ids


def _params(**overrides: object) -> ReportQueryParams:
    payload: dict[str, object] = {"from": date(2026, 1, 1), "to": date(2026, 6, 30)}
    payload.update(overrides)
    return ReportQueryParams.model_validate(payload)


def _leave_params(**overrides: object) -> LeaveReportParams:
    payload: dict[str, object] = {"from": date(2026, 1, 1), "to": date(2026, 6, 30)}
    payload.update(overrides)
    return LeaveReportParams.model_validate(payload)


def _admin() -> Identity:
    return Identity(tenant_id=TENANT, actor_id=USER, role=Role.ADMIN)


def _manager(employee_id: UUID | None = MANAGER) -> Identity:
    return Identity(
        tenant_id=TENANT,
        actor_id=USER,
        role=Role.MANAGER,
        employee_id=employee_id,
    )


def _service(
    repo: FakeRepository,
    *,
    labels: FakeLabels | None = None,
    scope: FakeScope | None = None,
) -> ReportsService:
    return ReportsService(
        repo=repo,
        labels=labels or FakeLabels({}),
        scope=scope or FakeScope(()),
    )


@pytest.mark.asyncio
async def test_overtime_metrics_and_labels() -> None:
    repo = FakeRepository(
        overtime=(
            OvertimeAggregateRow(
                bucket=date(2026, 2, 1),
                keys=(str(DEPARTMENT), str(LOCATION), "1_3y"),
                verified_hours=Decimal("5.00"),
                verified_request_count=2,
                verified_day_count=2,
                active_employee_count=3,
            ),
        )
    )
    labels = FakeLabels(
        {
            ("department", str(DEPARTMENT)): "Engineering",
            ("location", str(LOCATION)): "HQ",
        }
    )
    report = await _service(repo, labels=labels).overtime(
        _admin(),
        _params(group_by="department,location,seniority"),
    )

    [row] = report.rows
    assert row.groups[0].label == "Engineering"
    assert row.groups[1].label == "HQ"
    assert row.groups[2].label == "1–<3 years"
    # 5.00 / 3 = 1.666…, half-even at 2dp.
    assert row.hours_per_employee == Decimal("1.67")
    assert row.verified_hours == Decimal("5.00")
    assert report.group_by == [
        "department",
        "location",
        "seniority",
    ]
    assert report.model_dump(by_alias=True)["from"] == date(2026, 1, 1)
    assert report.model_dump(by_alias=True)["to"] == date(2026, 6, 30)

    # ADR 0019 decision 9: fixed-scale decimal strings on the wire.
    wire = report.model_dump(by_alias=True, mode="json")
    assert wire["rows"][0]["verified_hours"] == "5.00"
    assert wire["rows"][0]["hours_per_employee"] == "1.67"


@pytest.mark.asyncio
async def test_zero_denominators_are_zero_not_errors() -> None:
    repo = FakeRepository(
        overtime=(
            OvertimeAggregateRow(
                bucket=date(2026, 2, 1),
                keys=(str(DEPARTMENT),),
                verified_hours=Decimal("0.00"),
                verified_request_count=0,
                verified_day_count=0,
                active_employee_count=0,
            ),
        ),
        attendance=(
            AttendanceAggregateRow(
                bucket=date(2026, 2, 1),
                keys=(str(DEPARTMENT),),
                checkin_count=0,
                worked_day_count=0,
                worked_minutes=0,
                late_count=0,
                minutes_late_total=0,
            ),
        ),
    )
    service = _service(repo)

    overtime = await service.overtime(_admin(), _params())
    assert overtime.rows[0].hours_per_employee == Decimal("0.00")
    overtime_wire = overtime.model_dump(mode="json")["rows"][0]
    assert overtime_wire["hours_per_employee"] == "0.00"

    attendance = await service.attendance(_admin(), _params())
    assert attendance.rows[0].late_rate == Decimal("0.0000")
    assert attendance.rows[0].avg_minutes_late == Decimal("0.0")
    attendance_wire = attendance.model_dump(mode="json")["rows"][0]
    assert attendance_wire["late_rate"] == "0.0000"
    assert attendance_wire["avg_minutes_late"] == "0.0"


@pytest.mark.asyncio
async def test_attendance_ratios_round_half_even() -> None:
    repo = FakeRepository(
        attendance=(
            AttendanceAggregateRow(
                bucket=date(2026, 2, 1),
                keys=(str(DEPARTMENT),),
                checkin_count=3,
                worked_day_count=2,
                worked_minutes=910,
                late_count=1,
                minutes_late_total=10,
            ),
        )
    )
    report = await _service(repo).attendance(_admin(), _params())
    [row] = report.rows
    assert row.late_rate == Decimal("0.3333")  # 1/3, 4dp
    assert row.avg_minutes_late == Decimal("10.0")  # 10/1, 1dp
    assert row.worked_minutes == 910


@pytest.mark.asyncio
async def test_leave_groups_by_leave_type_with_reference_label() -> None:
    repo = FakeRepository(
        leave=(
            LeaveAggregateRow(
                bucket=date(2026, 2, 1),
                keys=("annual",),
                approved_day_count=5,
                approved_request_count=2,
            ),
        )
    )
    labels = FakeLabels({("leave_type", "annual"): "Annual leave"})
    report = await _service(repo, labels=labels).leave(
        _admin(), _leave_params(group_by="leave_type")
    )
    [row] = report.rows
    assert row.groups[0].key == "annual"
    assert row.groups[0].label == "Annual leave"


@pytest.mark.asyncio
async def test_unknown_and_unresolved_labels_fall_back() -> None:
    repo = FakeRepository(
        overtime=(
            OvertimeAggregateRow(
                bucket=None,
                keys=("unknown", str(DEPARTMENT)),
                verified_hours=Decimal("1.00"),
                verified_request_count=1,
                verified_day_count=1,
                active_employee_count=1,
            ),
        )
    )
    report = await _service(repo).overtime(
        _admin(), _params(group_by="department,seniority", interval="total")
    )
    [row] = report.rows
    assert row.bucket is None
    assert row.groups[0].label == "Unknown"
    assert row.groups[1].label == "Unknown"


@pytest.mark.asyncio
async def test_employee_role_is_denied_before_any_store_read() -> None:
    repo = FakeRepository()
    employee = Identity(
        tenant_id=TENANT,
        actor_id=USER,
        role=Role.EMPLOYEE,
        employee_id=REPORT,
    )
    with pytest.raises(ForbiddenError):
        await _service(repo).overtime(employee, _params())
    assert repo.ensure_ready_calls == 0


@pytest.mark.asyncio
async def test_manager_scope_is_self_plus_direct_reports() -> None:
    repo = FakeRepository()
    scope = FakeScope((MANAGER, REPORT))
    report = await _service(repo, scope=scope).overtime(_manager(), _params())

    assert report.rows == []
    assert scope.calls == [MANAGER]
    assert repo.last_query.employee_scope == (MANAGER, REPORT)
    assert repo.last_query.tenant_id == TENANT
    assert repo.last_query.employee_scope is not None


@pytest.mark.asyncio
async def test_unlinked_manager_gets_an_empty_scope_not_company_wide() -> None:
    repo = FakeRepository()
    scope = FakeScope(())
    await _service(repo, scope=scope).attendance(_manager(employee_id=None), _params())

    assert repo.last_query.employee_scope == ()
    assert repo.last_query.employee_scope is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.HR, Role.FINANCE, Role.ADMIN, Role.SUPER_ADMIN])
async def test_company_wide_roles_are_unscoped(role: Role) -> None:
    repo = FakeRepository()
    report_reader = Identity(tenant_id=TENANT, actor_id=USER, role=role)
    await _service(repo).headcount(report_reader, _params())
    assert repo.last_query.employee_scope is None


@pytest.mark.asyncio
async def test_unavailable_store_surfaces_the_domain_error() -> None:
    repo = FakeRepository(unavailable=True)
    with pytest.raises(ReportsUnavailableError):
        await _service(repo).overtime(_admin(), _params())


def test_interval_and_dimensions_are_parsed_in_order() -> None:
    params = _params(
        group_by="seniority,department",
        interval="week",
    )
    assert params.dimensions == ("seniority", "department")
    assert params.interval is ReportInterval.WEEK


def test_invalid_ranges_and_groupings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _params(**{"from": date(2026, 6, 1), "to": date(2026, 5, 1)})
    # Exactly 24 calendar months is allowed; a day beyond is rejected.
    assert _params(**{"from": date(2026, 1, 1), "to": date(2028, 1, 1)})
    with pytest.raises(ValidationError):
        _params(**{"from": date(2026, 1, 1), "to": date(2028, 1, 2)})
    with pytest.raises(ValidationError):
        _params(group_by="department,department")
    with pytest.raises(ValidationError):
        _params(group_by="department,unknown")
    with pytest.raises(ValidationError):
        _params(group_by="department,location,position,seniority")
    # leave_type is leave-only (PO Decision 8).
    with pytest.raises(ValidationError):
        _params(group_by="leave_type")
    # ... and valid on the leave report.
    assert _leave_params(group_by="leave_type").dimensions == ("leave_type",)
