"""Access policies for the Performance domain (`.ai/security.md`, T4).

Authorization lives here — never in routers, never only in the UI. Routers call
the guards before invoking a service; the service also calls them on its
mutation paths so a direct caller cannot bypass the role gate. Fail closed.

Matrix (`.ai/features/performance.md` → ## Roles & access):

- **Goals**: own for every employee role that can act (Employee), self + direct
  reports for Manager, anyone for HR/Admin/Super Admin. Finance has no
  management standing. Subject scope (own/team) is resolved by the service
  through the `EmployeeScope` port; out-of-scope ids are `404`, not `403`.
- **Reviews**: Manager (self + direct reports), HR, Admin, Super Admin. Employee
  and Finance are `403 performance_denied`. The subject is never the actor:
  `employee_id` equal to the caller's own link is `403` (`ensure_can_run_reviews`
  takes the subject so the never-self rule cannot be forgotten).
- **Feedback nomination / full feedback view**: the review author (Manager) and
  HR/Admin/Super Admin. A nominated provider — any role, including Employee or
  Finance — may file and see only their own comment; the subject never sees
  feedback (`feedback_access_denied`). Standing is review-specific, so
  `ensure_can_view_feedback` takes the resolved flags.
- **Skill catalog**: HR/Admin/Super Admin only. **Employee skills**: own for
  Employee, self + direct reports for Manager, anyone for HR/Admin/Super Admin.
"""

from uuid import UUID

from app.domains.performance.exceptions import (
    FeedbackAccessDeniedError,
    PerformanceDeniedError,
)
from app.shared.identity import Identity, Role

MANAGEMENT_ROLES = frozenset({Role.HR, Role.ADMIN, Role.SUPER_ADMIN})
# Goal authorship and employee-skill recording: self-service plus management.
GOAL_ROLES = MANAGEMENT_ROLES | {Role.EMPLOYEE, Role.MANAGER}
# Running reviews: managers (team-scoped) plus HR/Admin/Super Admin.
REVIEW_ROLES = MANAGEMENT_ROLES | {Role.MANAGER}
RECORD_SKILL_ROLES = GOAL_ROLES
CATALOG_ROLES = MANAGEMENT_ROLES


def is_management(identity: Identity) -> bool:
    """Whether the caller may manage performance without a subject scope."""
    return identity.role in MANAGEMENT_ROLES


def ensure_can_manage_goals(identity: Identity) -> None:
    """Goal mutations: Employee (own), Manager (team), HR/Admin/Super Admin."""
    if identity.role not in GOAL_ROLES:
        raise PerformanceDeniedError()


def ensure_can_run_reviews(
    identity: Identity, subject_employee_id: UUID | None = None
) -> None:
    """Review authoring: Manager/HR/Admin/Super Admin, and never self.

    When the subject is known (create), the rule is enforced here: an actor may
    never review the employee they are linked to. `None` is only for a pure
    capability check; mutation call sites pass the subject.
    """
    if identity.role not in REVIEW_ROLES:
        raise PerformanceDeniedError()
    if subject_employee_id is not None and identity.employee_id == subject_employee_id:
        raise PerformanceDeniedError()


def ensure_can_nominate(identity: Identity, *, is_author: bool) -> None:
    """Nominate providers: HR/Admin/Super Admin, or the review's own author.

    The matrix grants a Manager nomination rights on **their own** reviews only;
    `is_author` is resolved by the service from `review_author_id` before this
    guard runs. Employee and Finance never author reviews and are always
    refused, even when a stale `is_author` flag says otherwise.
    """
    if identity.role in MANAGEMENT_ROLES:
        return
    if identity.role is Role.MANAGER and is_author:
        return
    raise PerformanceDeniedError()


def ensure_can_manage_skills(identity: Identity) -> None:
    """The tenant skill catalog: HR/Admin/Super Admin only."""
    if identity.role not in CATALOG_ROLES:
        raise PerformanceDeniedError()


def ensure_can_record_skills(identity: Identity) -> None:
    """Employee-skill rows: own (Employee), team (Manager), anyone (HR+)."""
    if identity.role not in RECORD_SKILL_ROLES:
        raise PerformanceDeniedError()


def ensure_can_view_feedback(
    identity: Identity,
    *,
    is_author: bool,
    is_provider: bool,
    is_subject: bool,
) -> None:
    """Review feedback visibility, resolved per review.

    The subject is always refused (`feedback_access_denied`), even when they
    authored feedback for somebody else on a different review. The author and
    HR/Admin/Super Admin see every comment; a nominated provider sees only
    their own (the repository filters rows); anyone else is `performance_denied`.
    """
    if is_subject:
        raise FeedbackAccessDeniedError()
    if is_author or is_provider or identity.role in MANAGEMENT_ROLES:
        return
    raise PerformanceDeniedError()
