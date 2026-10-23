"""Analytics & Reports routes: the four reads pinned in the feature spec.

Thin by rule: identity + validated query params → service. The operationIds are
the orval hook names and must stay exactly as pinned (`getOvertimeReport`,
`getAttendanceReport`, `getLeaveReport`, `getHeadcountReport`). Every route is
read-only: policy lives in `domains/reports/policies.py`, manager scoping in the
service through the read-only ports, and nothing is audited or emitted.

`from`/`to` are the pinned wire names (aliased `start`/`end` in the schemas to
keep Python readable). Validation failures — `from > to`, a range over 24
months, a bad/duplicate/unsupported `group_by` — surface as the house `422
validation_error` before the service is reached.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies import IdentityDep, ReportsServiceDep
from app.domains.reports.schemas import (
    AttendanceReport,
    HeadcountReport,
    LeaveReport,
    LeaveReportParams,
    OvertimeReport,
    ReportQueryParams,
)

router = APIRouter()


@router.get(
    "/reports/overtime",
    response_model=OvertimeReport,
    operation_id="getOvertimeReport",
)
async def get_overtime_report(
    identity: IdentityDep,
    service: ReportsServiceDep,
    params: Annotated[ReportQueryParams, Query()],
) -> OvertimeReport:
    """Verified overtime by bucket and dimensions (HR-verified hours only)."""
    return await service.overtime(identity, params)


@router.get(
    "/reports/attendance",
    response_model=AttendanceReport,
    operation_id="getAttendanceReport",
)
async def get_attendance_report(
    identity: IdentityDep,
    service: ReportsServiceDep,
    params: Annotated[ReportQueryParams, Query()],
) -> AttendanceReport:
    """Attendance, worked time and lateness by bucket and dimensions."""
    return await service.attendance(identity, params)


@router.get(
    "/reports/leave",
    response_model=LeaveReport,
    operation_id="getLeaveReport",
)
async def get_leave_report(
    identity: IdentityDep,
    service: ReportsServiceDep,
    params: Annotated[LeaveReportParams, Query()],
) -> LeaveReport:
    """Approved leave by bucket and dimensions (`leave_type` allowed here)."""
    return await service.leave(identity, params)


@router.get(
    "/reports/headcount",
    response_model=HeadcountReport,
    operation_id="getHeadcountReport",
)
async def get_headcount_report(
    identity: IdentityDep,
    service: ReportsServiceDep,
    params: Annotated[ReportQueryParams, Query()],
) -> HeadcountReport:
    """Headcount at bucket end plus joiners/leavers for the bucket."""
    return await service.headcount(identity, params)
