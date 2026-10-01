"""Access policies for the Realtime Command Center (`.ai/security.md`).

Authorization lives here — never in routers, never only in the UI. The live
wall mirrors `GET /attendance/board`'s capability row exactly (spec decision
2): `manager` (self + direct reports scope), `hr`, `admin`, `super_admin`;
`employee` and `finance` are denied. The role set is duplicated here on
purpose — domains do not import each other's internals — and the REST/WS
edges are the only callers. Fail closed.
"""

from app.shared.errors import ForbiddenError
from app.shared.identity import Identity, Role

BOARD_ROLES = frozenset({Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN})


def ensure_can_read_presence(identity: Identity) -> None:
    """Snapshot and stream: the same roles as the attendance board."""
    if identity.role not in BOARD_ROLES:
        raise ForbiddenError("Not allowed to read the realtime board")
