"""Access policies for Analytics & Reports (`.ai/security.md`, spec rule 11).

Reports are read-only and role-scoped: HR / Finance / Admin / Super Admin see
company-wide numbers; a Manager sees the same reports restricted to the
self + direct-reports set that gates their leave/overtime visibility; an
Employee-role call is denied. The role gate lives here (AGENTS.md rule 12), the
manager's row scope is applied by the service through the `EmployeeScope` port.

Reports mutate nothing: no audit entry and no event is written on this path
(the `notification-inbox.md` read-state precedent). Fail closed.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

#: The five roles allowed to read reports (spec `## Roles & access`).
REPORT_READ_ROLES = frozenset(
    {
        Role.MANAGER,
        Role.HR,
        Role.FINANCE,
        Role.ADMIN,
        Role.SUPER_ADMIN,
    }
)


def ensure_can_read_reports(identity: Identity) -> None:
    """Deny Employee-role callers; managers are scoped later, not denied."""
    if identity.role not in REPORT_READ_ROLES:
        raise ForbiddenError("Not allowed to view reports")


__all__ = ["REPORT_READ_ROLES", "ensure_can_read_reports"]
