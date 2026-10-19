"""Access policies for the Leave domain (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service; the leave service also calls
`ensure_can_decide` on its decision paths so a direct caller cannot bypass the
role gate. Fail closed.

Role matrix (`.ai/features/leave.md`, ## Roles & access):

- request/withdraw: self-only for every role — resolved inside the service via
  `identity.employee_id`; there is no guard to call.
- view: own for `employee`/`finance`, self + direct reports for `manager`, all
  for `hr`/`admin`/`super_admin`. Scope narrowing lives in
  `LeaveService` (the attendance pattern); an out-of-scope request is a `404`.
- decide: `manager` (team only), `hr`, `admin`, `super_admin`. `employee` and
  `finance` have no decide capability at all. Self-approval is always a `403`
  and is checked in the service, where the request's owner is known.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

DECIDER_ROLES = frozenset({Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_decide(identity: Identity) -> None:
    """Approve/reject: manager (team scope), hr, admin, super_admin."""
    if identity.role not in DECIDER_ROLES:
        raise ForbiddenError("Not allowed to decide leave requests")
