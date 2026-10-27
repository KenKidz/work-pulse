"""Data-access abstractions for the Performance domain (T4).

Protocols only — the domain never imports infrastructure. Every method takes
`tenant_id` explicitly: tenant scoping is enforced in repository
implementations (`WHERE tenant_id = :tenant`), never in routers or services.
Repositories speak the pinned wire schemas (`schemas.py`); ORM translation
happens inside the Postgres adapter.

`EmployeeFacts` is the minimal employee read the rules need — active status for
`employee_not_active` and the manager link for the team scope. `EmployeeScope`
is the same self + direct-reports port leave/overtime/reports use, so a
manager's goal/review/skill visibility cannot drift from their inbox scope.
The Postgres adapter is reused (`PostgresEmployeeScope`); performance owns no
second copy of the query.

State changes are conditional updates on the observed source state: `None`
means the guard lost a concurrent race or the row is not in the tenant, and the
service maps that to the pinned wrong-state error (never a silent overwrite).
Provider churn and feedback submission lock the review row (`FOR UPDATE`) so a
`complete` and a late comment serialize instead of interleaving.

`GoalFilters.visible_employee_ids` / `ReviewFilters.visible_employee_ids` carry
the *effective* subject scope: `None` means "no subject scoping" (HR/Admin/Super
Admin), an empty sequence matches nothing. `ReviewFilters.author_id` adds the
caller's own authored reviews to the union (an author sees their reviews in
every state); `ReviewFilters.feedback_provider_id` is the nominated-provider
worklist and restricts the list to that provider's draft reviews.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domains.performance.schemas import (
    EmployeeSkill,
    Goal,
    GoalStatus,
    Review,
    ReviewFeedback,
    ReviewStatus,
    Skill,
)
from app.shared.pagination import PageParams


@dataclass(frozen=True, slots=True)
class EmployeeFacts:
    """Minimal employee facts performance rules need (id, active, manager)."""

    id: UUID
    active: bool
    manager_id: UUID | None


@dataclass(frozen=True, slots=True)
class SkillArchiveResult:
    """Outcome of an archive/restore call.

    `changed=False` is the ADR 0007 no-op: the skill was already in the target
    state, so the service returns `200` without an audit entry. `None` from the
    repository means the row is missing or foreign (`404 skill_not_found`).
    """

    skill: Skill
    changed: bool


class EmployeeScope(Protocol):
    """Read-only manager scope: self + direct reports (one level)."""

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> Sequence[UUID]:
        """The manager's visible employee ids; `[]` for an unlinked manager."""
        ...


class GoalFilters(BaseModel):
    """Effective goal-list filters (subject visibility already resolved)."""

    employee_id: UUID | None = None
    status: GoalStatus | None = None
    visible_employee_ids: Sequence[UUID] | None = None


class ReviewFilters(BaseModel):
    """Effective review-list filters (subject/author visibility resolved).

    `feedback_provider_id` is the peer worklist: only reviews the employee is
    nominated on and only while they are `draft` (the service rejects a
    conflicting `status` before calling).
    """

    employee_id: UUID | None = None
    status: ReviewStatus | None = None
    visible_employee_ids: Sequence[UUID] | None = None
    author_id: UUID | None = None
    feedback_provider_id: UUID | None = None


class SkillFilters(BaseModel):
    """Catalog filters: name substring + archived rows (hidden by default)."""

    q: str | None = None
    include_archived: bool = False


class PerformanceRepository(Protocol):
    # --- employee reads ------------------------------------------------------

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        """Tenant-scoped employee facts; `None` for a foreign or unknown id."""
        ...

    # --- goals ---------------------------------------------------------------

    async def create_goal(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        employee_id: UUID,
        title: str,
        description: str | None,
        due_date: date | None,
        progress_pct: int,
    ) -> Goal:
        """Insert a goal in `draft`; returns the pinned wire shape."""
        ...

    async def get_goal(self, tenant_id: UUID, goal_id: UUID) -> Goal | None:
        """Tenant-scoped detail; `None` for a missing or foreign id."""
        ...

    async def list_goals(
        self, tenant_id: UUID, filters: GoalFilters, page: PageParams
    ) -> tuple[Sequence[Goal], int]:
        """Paged goals, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def update_goal(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Goal | None:
        """Edit a `draft`/`active` goal (`WHERE status IN ('draft', 'active')`).

        `None` when the goal became terminal (lost race) or is not in the
        tenant; the service maps that to the pinned wrong-state error.
        """
        ...

    async def transition_goal_activate(
        self, tenant_id: UUID, goal_id: UUID, *, now: datetime
    ) -> Goal | None:
        """`draft → active` (`WHERE status = 'draft'`)."""
        ...

    async def transition_goal_complete(
        self, tenant_id: UUID, goal_id: UUID, *, now: datetime
    ) -> Goal | None:
        """`active → completed`, stamping `completed_at`."""
        ...

    async def transition_goal_cancel(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        from_status: GoalStatus,
        now: datetime,
    ) -> Goal | None:
        """`from_status (draft|active) → cancelled`, terminal.

        The observed source state is part of the update, so a concurrent
        `activate` cannot make the audit claim a transition the row did not
        have: a lost race returns `None` (the service maps it to
        `goal_terminal`).
        """
        ...

    # --- reviews -------------------------------------------------------------

    async def create_review(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        employee_id: UUID,
        author_id: UUID,
        period: str | None,
        rating: int | None,
        strengths: str | None,
        improvements: str | None,
        feedback_provider_ids: Sequence[UUID],
    ) -> Review:
        """Insert a draft review and its provider nominations atomically."""
        ...

    async def get_review(self, tenant_id: UUID, review_id: UUID) -> Review | None:
        """Tenant-scoped detail incl. `feedback_provider_ids`; `None` if foreign."""
        ...

    async def review_author_id(self, tenant_id: UUID, review_id: UUID) -> UUID | None:
        """The acting **user** id that authored the review (ADR 0010).

        `Review`'s pinned wire shape deliberately carries no `author_id`; the
        service needs it for author-only mutations and feedback visibility.
        `None` when the review is missing or foreign.
        """
        ...

    async def list_reviews(
        self, tenant_id: UUID, filters: ReviewFilters, page: PageParams
    ) -> tuple[Sequence[Review], int]:
        """Paged reviews, newest created first (`created_at`, `id` tiebreak)."""
        ...

    async def update_review(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Review | None:
        """Edit a `draft` review only; `None` when it is no longer draft/foreign."""
        ...

    async def replace_review_providers(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        employee_ids: Sequence[UUID],
        now: datetime,
    ) -> bool:
        """Replace the nomination set under the review row lock.

        `False` when the review is missing, foreign or no longer `draft` — the
        service maps that to `404`/`409 review_not_draft`.
        """
        ...

    async def feedback_author_ids(
        self, tenant_id: UUID, review_id: UUID
    ) -> Sequence[UUID]:
        """Employee ids that already filed feedback on the review."""
        ...

    async def transition_review_complete(
        self, tenant_id: UUID, review_id: UUID, *, now: datetime
    ) -> Review | None:
        """`draft → completed` with a rating present, stamping `completed_at`."""
        ...

    async def transition_review_cancel(
        self, tenant_id: UUID, review_id: UUID, *, now: datetime
    ) -> Review | None:
        """`draft → cancelled`, terminal."""
        ...

    # --- feedback ------------------------------------------------------------

    async def create_feedback(
        self,
        tenant_id: UUID,
        feedback_id: UUID,
        *,
        review_id: UUID,
        author_id: UUID,
        comment: str,
        now: datetime,
    ) -> ReviewFeedback | None:
        """Insert one comment while the review is `draft` (row-locked).

        `None` when the review is missing, foreign or no longer draft; a second
        comment by the same provider raises `409 feedback_already_submitted`
        from the unique constraint (mapped in the adapter).
        """
        ...

    async def list_feedback(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        author_id: UUID | None = None,
    ) -> Sequence[ReviewFeedback]:
        """Feedback on the review, oldest first; `author_id` filters to one peer."""
        ...

    # --- skill catalog -------------------------------------------------------

    async def create_skill(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        name: str,
        description: str | None,
    ) -> Skill:
        """Insert a catalog skill; a duplicate name raises `409 skill_name_taken`."""
        ...

    async def get_skill(self, tenant_id: UUID, skill_id: UUID) -> Skill | None:
        """Tenant-scoped detail; `None` for a missing or foreign id."""
        ...

    async def list_skills(
        self, tenant_id: UUID, filters: SkillFilters, page: PageParams
    ) -> tuple[Sequence[Skill], int]:
        """Paged catalog, name-ordered; archived rows only on request."""
        ...

    async def update_skill(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Skill | None:
        """Rename/re-describe; a duplicate name raises `409 skill_name_taken`."""
        ...

    async def archive_skill(
        self, tenant_id: UUID, skill_id: UUID, *, now: datetime
    ) -> SkillArchiveResult | None:
        """Soft delete (`archived_at`); already-archived is a `changed=False` no-op."""
        ...

    async def restore_skill(
        self, tenant_id: UUID, skill_id: UUID, *, now: datetime
    ) -> SkillArchiveResult | None:
        """Clear `archived_at`; already-active is a `changed=False` no-op."""
        ...

    # --- employee skills -----------------------------------------------------

    async def record_employee_skill(
        self,
        tenant_id: UUID,
        *,
        employee_id: UUID,
        skill_id: UUID,
        proficiency: int,
        note: str | None,
    ) -> EmployeeSkill:
        """Attach a skill; a second attach raises `409 employee_skill_exists`."""
        ...

    async def list_employee_skills(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[EmployeeSkill]:
        """Every skill row for the employee, skill-name ordered."""
        ...

    async def get_employee_skill(
        self, tenant_id: UUID, employee_id: UUID, skill_id: UUID
    ) -> EmployeeSkill | None:
        """One employee-skill row; `None` when not attached."""
        ...

    async def update_employee_skill(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        skill_id: UUID,
        *,
        proficiency: int,
        note: str | None,
        now: datetime,
    ) -> EmployeeSkill | None:
        """Replace proficiency/note in place; `None` when not attached."""
        ...

    async def remove_employee_skill(
        self, tenant_id: UUID, employee_id: UUID, skill_id: UUID
    ) -> bool:
        """Delete the row; `False` when it was not attached."""
        ...
