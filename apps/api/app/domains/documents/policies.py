"""Access policies for the Documents domain (`.ai/security.md`, T3).

Authorization lives here — never in routers, never only in the UI. The service
calls these guards on every path, so a direct caller cannot bypass the role
gate. Denials reuse `app.shared.errors.ForbiddenError` (`403 forbidden`) so
every domain reports the house code; foreign, unknown and cross-tenant ids are
resolved to `404` by the service before or after this check, never leaked as
`403`.

Matrix (`.ai/features/employee-documents.md` → ## Roles & access):

- **Manage** (upload intent, confirm, metadata update, delete): HR / Admin /
  Super Admin only. Never a manager, never self-service, never Finance.
- **Read** (list, get, download): the owner (any role — their own documents,
  the "My documents" surface); a manager over self + direct reports (the
  `EmployeeScope` port, one level, resolved by the service); HR / Admin /
  Super Admin everyone; Finance their own only. A same-tenant out-of-scope
  read is `403`; the router/service maps a foreign or unknown employee id to
  `404 employee_not_found` first.
"""

from collections.abc import Collection
from uuid import UUID

from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

MANAGEMENT_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_manage(identity: Identity) -> None:
    """Writes: HR / Admin / Super Admin only."""
    if identity.role not in MANAGEMENT_ROLES:
        raise ForbiddenError()


def ensure_can_read(
    identity: Identity,
    target_employee_id: UUID,
    visible_employee_ids: Collection[UUID],
) -> None:
    """Reads: owner, a manager's team, or the management roles.

    `visible_employee_ids` is the manager's resolved `EmployeeScope` result
    (self + direct reports, one level) — empty for every other calling role.
    The owner check comes first so an employee always reaches their own
    documents regardless of role; management roles pass everyone.
    """
    if identity.employee_id is not None and identity.employee_id == target_employee_id:
        return
    if identity.role in MANAGEMENT_ROLES:
        return
    if identity.role is Role.MANAGER and target_employee_id in visible_employee_ids:
        return
    raise ForbiddenError()
