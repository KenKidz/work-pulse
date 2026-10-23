"""Access policies for the Recruitment domain (`.ai/security.md`, T2).

Authorization lives here — never in routers, never only in the UI. Routers
call the guards before invoking a service; the service also calls them on its
mutation paths so a direct caller cannot bypass the role gate. Fail closed.

Matrix (`.ai/features/recruitment.md` → ## Roles & access):

- manage jobs/candidates/hire/schedule: HR, Admin, Super Admin only
  (`MANAGEMENT_ROLES`); Employee, Manager and Finance are all `403
  recruitment_denied` on a management mutation.
- read jobs/candidates: management sees everything; an assigned interviewer
  (any role) sees only what their interviews make visible — the service applies
  that scope, and out-of-scope entity ids are `404`, not `403`.
- Employee and Finance have no recruitment standing at all: they are refused
  at the endpoint (`403 recruitment_denied`), including reads.
"""

from app.domains.recruitment.exceptions import RecruitmentDeniedError
from app.shared.identity import Identity, Role

MANAGEMENT_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})
# Roles allowed to touch the recruitment surface at all. Managers only ever
# see their own interview-derived slice (resolved in the service).
READ_ROLES = MANAGEMENT_ROLES | {Role.MANAGER}


def is_management(identity: Identity) -> bool:
    """Whether the caller may manage recruitment (HR/Admin/Super Admin)."""
    return identity.role in MANAGEMENT_ROLES


def ensure_can_manage(identity: Identity) -> None:
    """Management mutations: HR, Admin, Super Admin; everyone else is `403`."""
    if not is_management(identity):
        raise RecruitmentDeniedError()


def ensure_has_standing(identity: Identity) -> None:
    """Reads: any role with a recruitment view (management or scoped interviewer)."""
    if identity.role not in READ_ROLES:
        raise RecruitmentDeniedError()
