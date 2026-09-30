"""Attendance routes: the 7 routes pinned in `.ai/features/attendance.md`.

Thin by rule: identity → policy → service; `now` is taken at this edge only
(`_now()` is the single wall-clock read). operationIds are pinned and drive the
orval-generated web hooks.

Two routers live here on purpose: the attendance routes carry the `attendance`
tag, the shift reference-data routes the `shifts` tag (T1). Within the
attendance router, `/attendance/records/{id}/check-out` is declared before the
`/attendance/records` list route so the path can never be parsed as a record
id. The board's `?date=` defaults to the server's current date in UTC (T1 —
there is no tenant-level timezone in Phase 1).
"""

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.dependencies import AttendanceServiceDep, IdentityDep, ShiftServiceDep
from app.domains.attendance.policies import (
    ensure_can_manage_shifts,
    ensure_can_read_board,
    ensure_can_read_records,
)
from app.domains.attendance.schemas import (
    AttendanceRecord,
    AttendanceRecordListPage,
    Board,
    CheckInRequest,
    CheckOutRequest,
    Shift,
    ShiftCreate,
    ShiftList,
    ShiftUpdate,
)
from app.shared.pagination import PageParamsDep

router = APIRouter()
shifts_router = APIRouter()


def _now() -> datetime:
    """The single place wall-clock time is read (time is a parameter below)."""
    return datetime.now(UTC)


# --- attendance --------------------------------------------------------------


@router.post(
    "/attendance/check-ins",
    response_model=AttendanceRecord,
    operation_id="checkIn",
    status_code=201,
)
async def check_in(
    payload: CheckInRequest,
    identity: IdentityDep,
    service: AttendanceServiceDep,
) -> AttendanceRecord:
    """Open a record for the actor — self-only, server time only (Decision #3)."""
    return (await service.check_in(identity, payload, _now())).record


@router.post(
    "/attendance/records/{id}/check-out",
    response_model=AttendanceRecord,
    operation_id="checkOut",
)
async def check_out(
    id: UUID,
    payload: CheckOutRequest,
    identity: IdentityDep,
    service: AttendanceServiceDep,
) -> AttendanceRecord:
    """Close the actor's open record; the workday is never recomputed."""
    return (await service.check_out(identity, id, payload, _now())).record


@router.get(
    "/attendance/records",
    response_model=AttendanceRecordListPage,
    operation_id="listAttendanceRecords",
)
async def list_attendance_records(
    identity: IdentityDep,
    service: AttendanceServiceDep,
    page: PageParamsDep,
    employee_id: UUID | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    late: bool | None = Query(default=None),
) -> AttendanceRecordListPage:
    """Filtered records, scoped to the actor's visibility (T1 role matrix)."""
    ensure_can_read_records(identity)
    return await service.list_records(
        identity,
        employee_id=employee_id,
        from_date=from_,
        to_date=to,
        late=late,
        page=page,
    )


@router.get(
    "/attendance/board",
    response_model=Board,
    operation_id="getAttendanceBoard",
)
async def get_attendance_board(
    identity: IdentityDep,
    service: AttendanceServiceDep,
    date_: date | None = Query(default=None, alias="date"),
) -> Board:
    """Live workforce board for one date (default: the server's UTC date)."""
    ensure_can_read_board(identity)
    now = _now()
    return await service.board(identity, date_ or now.date(), now)


# --- shifts ------------------------------------------------------------------


@shifts_router.get(
    "/shifts",
    response_model=ShiftList,
    operation_id="listShifts",
)
async def list_shifts(
    identity: IdentityDep,
    service: ShiftServiceDep,
) -> ShiftList:
    """Bare unpaged array, ordered `location_id` nulls-last then `name` (T1)."""
    ensure_can_manage_shifts(identity)
    return await service.list(identity)


@shifts_router.post(
    "/shifts",
    response_model=Shift,
    operation_id="createShift",
    status_code=201,
)
async def create_shift(
    payload: ShiftCreate,
    identity: IdentityDep,
    service: ShiftServiceDep,
) -> Shift:
    ensure_can_manage_shifts(identity)
    return await service.create(identity, payload, _now())


@shifts_router.patch(
    "/shifts/{id}",
    response_model=Shift,
    operation_id="updateShift",
)
async def update_shift(
    id: UUID,
    payload: ShiftUpdate,
    identity: IdentityDep,
    service: ShiftServiceDep,
) -> Shift:
    ensure_can_manage_shifts(identity)
    return await service.update(identity, id, payload, _now())
