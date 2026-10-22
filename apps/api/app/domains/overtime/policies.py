"""Access policies for the Overtime domain (`.ai/security.md`, T4).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service; the overtime service also calls
them on its decision paths so a direct caller cannot bypass the role gate.
Fail closed.

Two-step role matrix (`.ai/features/overtime.md`, ## Roles & access and PO
Decision 5):

- create / view own / withdraw: self-only for every role — resolved inside the
  service via `identity.employee_id`; there is no guard to call.
- view: own for `employee`/`finance`, self + direct reports for `manager`, all
  for `hr`/`admin`/`super_admin`. Scope narrowing lives in `OvertimeService`;
  an out-of-scope request is a `404`.
- approve (manager step, from `requested`) and reject from `requested`:
  `manager` (team only), `hr`, `admin`, `super_admin`.
- verify (HR step, from `manager_approved`) and reject from
  `manager_approved`/`hr_verified`: `hr`, `admin`, `super_admin` only — a
  manager without an HR role cannot verify.
- self-decision is always a `403` and is checked in the service, where the
  request's owner is known.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

MANAGER_STEP_ROLES = frozenset({Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN})
HR_STEP_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_approve(identity: Identity) -> None:
    """Manager step: manager (team scope), hr, admin, super_admin."""
    if identity.role not in MANAGER_STEP_ROLES:
        raise ForbiddenError("Not allowed to approve overtime requests")


def ensure_can_verify(identity: Identity) -> None:
    """HR step: hr, admin, super_admin — managers without HR may not verify."""
    if identity.role not in HR_STEP_ROLES:
        raise ForbiddenError("Not allowed to verify overtime requests")
