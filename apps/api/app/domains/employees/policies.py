"""Access policies for the Employees domain (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service. Fail closed.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

MANAGE_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})
READ_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN, Role.MANAGER})


def ensure_can_manage_employees(identity: Identity) -> None:
    """Create/update/transfer/terminate: hr, admin, super_admin only."""
    if identity.role not in MANAGE_ROLES:
        raise ForbiddenError("Not allowed to manage employees")


def ensure_can_read_employees(identity: Identity) -> None:
    """List/read employees: hr, admin, super_admin (all) · manager (own reports).

    Team-level narrowing for managers happens in the service; the guard only
    decides whether the role may read employees at all.
    """
    if identity.role not in READ_ROLES:
        raise ForbiddenError("Not allowed to read employees")


def ensure_can_read_self(identity: Identity) -> None:
    """`/me` is readable by every valid role (identity already validated)."""
