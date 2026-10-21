"""Access policies for the Payroll domain (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service; the payroll service also calls
them on its mutating paths so a direct caller cannot bypass the role gate.
Fail closed.

Role matrix (`.ai/features/payroll.md` → Roles & access):

- configure (config, deduction lines, salary records): finance, admin,
  super_admin.
- run (create/recalculate/finalize, bonuses): finance, admin, super_admin.
  HR deliberately has **view** only, so an HR finalize is a `403`.
- view runs/timesheets/all payslips: HR, finance, admin, super_admin.
- view a payslip: the owning employee (own only), plus the four view roles.
  A manager has no team payroll visibility.

Unknown, cross-tenant or out-of-scope reads are `404`, never `403`, so the API
does not leak existence. A caller with standing who attempts a forbidden
transition is `403` (e.g. HR finalizing a run).
"""

from uuid import UUID

from app.domains.payroll.exceptions import (
    PayrollNotFoundError,
    PayrollRunNotFoundError,
    PayslipNotFoundError,
)
from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

# Configure/run: the "Configure & run payroll" matrix row (HR is view-only).
PAYROLL_OPERATOR_ROLES = frozenset({Role.FINANCE, Role.ADMIN, Role.SUPER_ADMIN})

# Company-wide payroll visibility: runs, timesheets and every payslip.
PAYROLL_VIEW_ROLES = frozenset({Role.HR, Role.FINANCE, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_configure(identity: Identity) -> None:
    """Config, deduction lines and salary-record writes."""
    if identity.role not in PAYROLL_OPERATOR_ROLES:
        raise ForbiddenError("Not allowed to configure payroll")


def ensure_can_run(identity: Identity) -> None:
    """Run lifecycle writes and per-run bonuses (HR is view-only)."""
    if identity.role not in PAYROLL_OPERATOR_ROLES:
        raise ForbiddenError("Not allowed to run payroll")


def ensure_can_view_runs(identity: Identity) -> None:
    """HR/finance/admin/super_admin only; everyone else gets a `404`."""
    if identity.role not in PAYROLL_VIEW_ROLES:
        raise PayrollRunNotFoundError()


def ensure_can_view_payroll(identity: Identity) -> None:
    """Config, deduction lines and salary records: the company-wide readers.

    HR reads them (view-only); employee/manager have no payroll workspace and
    receive the same `404` as an unknown resource.
    """
    if identity.role not in PAYROLL_VIEW_ROLES:
        raise PayrollNotFoundError()


def ensure_can_view_payslip(identity: Identity, employee_id: UUID) -> None:
    """Own payslip, or any payslip for the company-wide view roles.

    Managers and employees opening another employee's payslip receive the same
    `404 payslip_not_found` as an unknown id (existence is not leaked); an
    account without an employee link can never match "own".
    """
    if identity.role in PAYROLL_VIEW_ROLES:
        return
    if identity.employee_id is not None and identity.employee_id == employee_id:
        return
    raise PayslipNotFoundError()
