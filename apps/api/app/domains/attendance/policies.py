"""Access policies for the Attendance domain (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service. Fail closed.

T1 role matrix (`.ai/features/attendance.md`):

- records: every valid role reads records — `employee`/`finance` only their
  own, `manager` self + direct reports, `hr`/`admin`/`super_admin` all. The
  scope narrowing lives in `AttendanceService`; the guard only decides whether
  the role may read records at all (all of them may).
- board: `manager` (self + reports scope), `hr`, `admin`, `super_admin`.
- shifts: `hr`/`admin`/`super_admin` only.
- check-in/check-out is self-only for every role: the actor's own employee is
  resolved from the identity inside the service — there is no guard to call.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import DevIdentity, Role

BOARD_ROLES = frozenset({Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN})
SHIFT_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_read_records(identity: DevIdentity) -> None:
    """List attendance records: allowed for every valid role.

    `employee`/`finance` are server-scoped to their own records by the
    service; a denied role does not exist for this route (T1).
    """


def ensure_can_read_board(identity: DevIdentity) -> None:
    """Live board: manager (self + reports), hr, admin, super_admin."""
    if identity.role not in BOARD_ROLES:
        raise ForbiddenError("Not allowed to read the attendance board")


def ensure_can_manage_shifts(identity: DevIdentity) -> None:
    """Create/update shifts: hr, admin, super_admin only."""
    if identity.role not in SHIFT_ROLES:
        raise ForbiddenError("Not allowed to manage shifts")
