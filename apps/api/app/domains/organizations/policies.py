"""Access policies for the Organizations domain (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service. Fail closed.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import DevIdentity, Role

MANAGE_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_read(identity: DevIdentity) -> None:
    """Any valid role may read organization reference data.

    The identity dependency has already failed closed on missing/malformed
    headers and unknown roles, so a `DevIdentity` instance is always allowed.
    """


def ensure_can_manage(identity: DevIdentity) -> None:
    """Create/update/archive/restore: hr, admin, super_admin only."""
    if identity.role not in MANAGE_ROLES:
        raise ForbiddenError("Not allowed to manage organization data")
