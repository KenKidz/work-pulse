"""Access policies for the Identity domain (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. Routers
call these guards before invoking a service. Fail closed.

Login, logout, `GET /auth/me` and changing one's own password are authenticated
by the session itself and need no role policy; every user-management route
requires the "Manage users, roles, permissions" capability.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import DevIdentity, Role

# The admin role set, defined once for the whole domain: the user-management
# policy, the `last_admin` guard in the service, and the active-admin count in
# the repositories all derive from it, so they cannot drift apart.
ADMIN_ROLES = frozenset({Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_manage_users(identity: DevIdentity) -> None:
    """List/read/create/update/activate/deactivate/reset: admin, super_admin."""
    if identity.role not in ADMIN_ROLES:
        raise ForbiddenError("Not allowed to manage users")
