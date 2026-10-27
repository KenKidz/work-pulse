"""In-memory fakes for the Performance domain unit tests (T4).

No I/O anywhere (`.ai/testing.md` — these tests pass with `postgres-test`
stopped). `FakePerformanceRepo` honors tenant scope, the pinned list filters,
the conditional transition guards, provider churn and the unique constraints the
Postgres adapter backs with indexes/primary keys, so the T5–T7 services are
exercised against the same rules without simulating transactions.

Transitions return `None` when their observed source state no longer matches —
exactly the backstop the service maps to the pinned wrong-state error.
`lose_next_transition` simulates the concurrent-editor race: the next
conditional write loses even though the service's pre-check passed.

`FakeEmployeeScope` resolves the same self + direct-reports set as the real
`PostgresEmployeeScope`; passing `repo.employees` makes both share one directory
(the conftest wires that by default).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domains.performance.exceptions import (
    EmployeeSkillExistsError,
    FeedbackAlreadySubmittedError,
    PerformanceDeniedError,
    SkillNameTakenError,
    SkillNotFoundError,
)
from app.domains.performance.repository import (
    EmployeeFacts,
    GoalFilters,
    ReviewFilters,
    ReviewRecord,
    SkillArchiveResult,
    SkillFilters,
)
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

CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
_OPEN_GOAL_STATUSES = (GoalStatus.DRAFT, GoalStatus.ACTIVE)


class FakePerformanceRepo:
    """Tenant-scoped in-memory implementation of `PerformanceRepository`."""

    def __init__(self) -> None:
        # (tenant, id) → row. The wire models carry no tenant; the key does.
        self.goals: dict[tuple[UUID, UUID], Goal] = {}
        self.reviews: dict[tuple[UUID, UUID], Review] = {}
        # The pinned `Review` wire shape omits the author user id; keep it in a
        # side map exactly as the Postgres adapter keeps the column.
        self.review_authors: dict[tuple[UUID, UUID], UUID] = {}
        self.feedback: dict[tuple[UUID, UUID], ReviewFeedback] = {}
        self.skills: dict[tuple[UUID, UUID], Skill] = {}
        # (tenant, employee, skill) → row, mirroring the composite primary key.
        self.employee_skills: dict[tuple[UUID, UUID, UUID], EmployeeSkill] = {}
        # (tenant, employee) → facts — shared with `FakeEmployeeScope`.
        self.employees: dict[tuple[UUID, UUID], EmployeeFacts] = {}
        # (tenant, user) → employee link, mirroring `users.employee_id`.
        self.user_employees: dict[tuple[UUID, UUID], UUID | None] = {}
        self.calls: list[str] = []
        self.lose_next_transition = False
        self._created = 0

    def _race_lost(self) -> bool:
        """Consume a one-shot simulated lost race (the UPDATE matched 0 rows)."""
        if not self.lose_next_transition:
            return False
        self.lose_next_transition = False
        return True

    def _stamp(self) -> datetime:
        self._created += 1
        return CREATED_AT + timedelta(seconds=self._created)

    # --- employee reads ------------------------------------------------------

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        return self.employees.get((tenant_id, employee_id))

    async def employee_facts_many(
        self, tenant_id: UUID, employee_ids: Sequence[UUID]
    ) -> dict[UUID, EmployeeFacts]:
        facts: dict[UUID, EmployeeFacts] = {}
        for employee_id in dict.fromkeys(employee_ids):
            row = self.employees.get((tenant_id, employee_id))
            if row is not None:
                facts[employee_id] = row
        return facts

    async def user_employee_id(self, tenant_id: UUID, user_id: UUID) -> UUID | None:
        return self.user_employees.get((tenant_id, user_id))

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
        self.calls.append("create_goal")
        created_at = self._stamp()
        row = Goal(
            id=goal_id,
            employee_id=employee_id,
            title=title,
            description=description,
            due_date=due_date,
            progress_pct=progress_pct,
            status=GoalStatus.DRAFT,
            created_at=created_at,
            updated_at=created_at,
        )
        self.goals[(tenant_id, goal_id)] = row
        return row

    async def get_goal(self, tenant_id: UUID, goal_id: UUID) -> Goal | None:
        return self.goals.get((tenant_id, goal_id))

    async def list_goals(
        self, tenant_id: UUID, filters: GoalFilters, page: PageParams
    ) -> tuple[Sequence[Goal], int]:
        if (
            filters.visible_employee_ids is not None
            and not filters.visible_employee_ids
        ):
            return [], 0
        allowed = (
            None
            if filters.visible_employee_ids is None
            else set(filters.visible_employee_ids)
        )
        rows = [
            row
            for (row_tenant, _), row in self.goals.items()
            if row_tenant == tenant_id
            and (allowed is None or row.employee_id in allowed)
            and (filters.employee_id is None or row.employee_id == filters.employee_id)
            and (filters.status is None or row.status == filters.status)
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def update_goal(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Goal | None:
        self.calls.append("update_goal")
        if self._race_lost():
            return None
        key = (tenant_id, goal_id)
        row = self.goals.get(key)
        if row is None or row.status not in _OPEN_GOAL_STATUSES:
            return None
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.goals[key] = updated
        return updated

    async def transition_goal_activate(
        self, tenant_id: UUID, goal_id: UUID, *, now: datetime
    ) -> Goal | None:
        self.calls.append("transition_goal_activate")
        if self._race_lost():
            return None
        key = (tenant_id, goal_id)
        row = self.goals.get(key)
        if row is None or row.status is not GoalStatus.DRAFT:
            return None
        updated = row.model_copy(
            update={"status": GoalStatus.ACTIVE, "updated_at": now}
        )
        self.goals[key] = updated
        return updated

    async def transition_goal_complete(
        self, tenant_id: UUID, goal_id: UUID, *, now: datetime
    ) -> Goal | None:
        self.calls.append("transition_goal_complete")
        if self._race_lost():
            return None
        key = (tenant_id, goal_id)
        row = self.goals.get(key)
        if row is None or row.status is not GoalStatus.ACTIVE:
            return None
        updated = row.model_copy(
            update={
                "status": GoalStatus.COMPLETED,
                "completed_at": now,
                "updated_at": now,
            }
        )
        self.goals[key] = updated
        return updated

    async def transition_goal_cancel(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        from_status: GoalStatus,
        now: datetime,
    ) -> Goal | None:
        self.calls.append("transition_goal_cancel")
        if self._race_lost():
            return None
        key = (tenant_id, goal_id)
        row = self.goals.get(key)
        if row is None or row.status is not from_status:
            return None
        updated = row.model_copy(
            update={"status": GoalStatus.CANCELLED, "updated_at": now}
        )
        self.goals[key] = updated
        return updated

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
        self.calls.append("create_review")
        created_at = self._stamp()
        row = Review(
            id=review_id,
            employee_id=employee_id,
            period=period,
            rating=rating,
            strengths=strengths,
            improvements=improvements,
            status=ReviewStatus.DRAFT,
            feedback_provider_ids=list(dict.fromkeys(feedback_provider_ids)),
            created_at=created_at,
            updated_at=created_at,
        )
        self.reviews[(tenant_id, review_id)] = row
        self.review_authors[(tenant_id, review_id)] = author_id
        return row

    async def get_review(self, tenant_id: UUID, review_id: UUID) -> Review | None:
        return self.reviews.get((tenant_id, review_id))

    async def get_review_with_author(
        self, tenant_id: UUID, review_id: UUID
    ) -> ReviewRecord | None:
        review = self.reviews.get((tenant_id, review_id))
        author_id = self.review_authors.get((tenant_id, review_id))
        if review is None or author_id is None:
            return None
        return ReviewRecord(review=review, author_id=author_id)

    async def list_reviews(
        self, tenant_id: UUID, filters: ReviewFilters, page: PageParams
    ) -> tuple[Sequence[Review], int]:
        rows: list[Review] = []
        for (row_tenant, _), row in self.reviews.items():
            if row_tenant != tenant_id:
                continue
            if filters.feedback_provider_id is not None:
                if (
                    filters.feedback_provider_id
                    not in (row.feedback_provider_ids or [])
                    or row.status is not ReviewStatus.DRAFT
                ):
                    continue
            else:
                grant = False
                if filters.visible_employee_ids is not None:
                    grant = row.employee_id in set(filters.visible_employee_ids)
                if filters.completed_only_employee_ids is not None:
                    grant = grant or (
                        row.employee_id in set(filters.completed_only_employee_ids)
                        and row.status is ReviewStatus.COMPLETED
                    )
                if filters.author_id is not None:
                    author_id = self.review_authors.get((row_tenant, row.id))
                    grant = grant or author_id == filters.author_id
                if (
                    filters.visible_employee_ids is not None
                    or filters.completed_only_employee_ids is not None
                    or filters.author_id is not None
                ) and not grant:
                    continue
            if (
                filters.employee_id is not None
                and row.employee_id != filters.employee_id
            ):
                continue
            if filters.hidden_non_completed_employee_ids and (
                row.employee_id in set(filters.hidden_non_completed_employee_ids)
                and row.status is not ReviewStatus.COMPLETED
            ):
                continue
            if filters.status is not None and row.status != filters.status:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def update_review(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Review | None:
        self.calls.append("update_review")
        if "feedback_provider_ids" in changes:
            raise ValueError(
                "feedback_provider_ids must be changed via replace_review_providers"
            )
        if self._race_lost():
            return None
        key = (tenant_id, review_id)
        row = self.reviews.get(key)
        if row is None or row.status is not ReviewStatus.DRAFT:
            return None
        updated = row.model_copy(update={**changes, "updated_at": now})
        self.reviews[key] = updated
        return updated

    async def replace_review_providers(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        employee_ids: Sequence[UUID],
        now: datetime,
    ) -> bool:
        self.calls.append("replace_review_providers")
        if self._race_lost():
            return False
        key = (tenant_id, review_id)
        row = self.reviews.get(key)
        if row is None or row.status is not ReviewStatus.DRAFT:
            return False
        current = set(row.feedback_provider_ids or [])
        removed = current - set(employee_ids)
        if removed:
            filed = {
                feedback.author_id
                for (feedback_tenant, _), feedback in self.feedback.items()
                if feedback_tenant == tenant_id
                and feedback.review_id == review_id
                and feedback.author_id in removed
            }
            if filed:
                raise FeedbackAlreadySubmittedError()
        updated = row.model_copy(
            update={
                "feedback_provider_ids": list(dict.fromkeys(employee_ids)),
                "updated_at": now,
            }
        )
        self.reviews[key] = updated
        return True

    async def feedback_author_ids(
        self, tenant_id: UUID, review_id: UUID
    ) -> Sequence[UUID]:
        return sorted(
            row.author_id
            for (row_tenant, _), row in self.feedback.items()
            if row_tenant == tenant_id and row.review_id == review_id
        )

    async def transition_review_complete(
        self, tenant_id: UUID, review_id: UUID, *, now: datetime
    ) -> Review | None:
        self.calls.append("transition_review_complete")
        if self._race_lost():
            return None
        key = (tenant_id, review_id)
        row = self.reviews.get(key)
        if row is None or row.status is not ReviewStatus.DRAFT or row.rating is None:
            return None
        updated = row.model_copy(
            update={
                "status": ReviewStatus.COMPLETED,
                "completed_at": now,
                "updated_at": now,
            }
        )
        self.reviews[key] = updated
        return updated

    async def transition_review_cancel(
        self, tenant_id: UUID, review_id: UUID, *, now: datetime
    ) -> Review | None:
        self.calls.append("transition_review_cancel")
        if self._race_lost():
            return None
        key = (tenant_id, review_id)
        row = self.reviews.get(key)
        if row is None or row.status is not ReviewStatus.DRAFT:
            return None
        updated = row.model_copy(
            update={"status": ReviewStatus.CANCELLED, "updated_at": now}
        )
        self.reviews[key] = updated
        return updated

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
        self.calls.append("create_feedback")
        if self._race_lost():
            return None
        review = self.reviews.get((tenant_id, review_id))
        if review is None or review.status is not ReviewStatus.DRAFT:
            return None
        if review.employee_id == author_id:
            raise PerformanceDeniedError()
        if author_id not in (review.feedback_provider_ids or []):
            raise PerformanceDeniedError()
        if any(
            row_tenant == tenant_id
            and row.review_id == review_id
            and row.author_id == author_id
            for (row_tenant, _), row in self.feedback.items()
        ):
            raise FeedbackAlreadySubmittedError()
        row = ReviewFeedback(
            id=feedback_id,
            review_id=review_id,
            author_id=author_id,
            comment=comment,
            created_at=now,
        )
        self.feedback[(tenant_id, feedback_id)] = row
        return row

    async def list_feedback(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        author_id: UUID | None = None,
    ) -> Sequence[ReviewFeedback]:
        rows = [
            row
            for (row_tenant, _), row in self.feedback.items()
            if row_tenant == tenant_id
            and row.review_id == review_id
            and (author_id is None or row.author_id == author_id)
        ]
        rows.sort(key=lambda row: (row.created_at, row.id))
        return rows

    # --- skill catalog -------------------------------------------------------

    def _skill_name_taken(
        self, tenant_id: UUID, name: str, *, exclude_id: UUID | None = None
    ) -> bool:
        """Catalog uniqueness spans archived rows (ADR 0007)."""
        return any(
            row_tenant == tenant_id and row.name == name and row.id != exclude_id
            for (row_tenant, _), row in self.skills.items()
        )

    async def skill_name_taken(
        self, tenant_id: UUID, name: str, *, exclude_id: UUID | None = None
    ) -> bool:
        return self._skill_name_taken(tenant_id, name, exclude_id=exclude_id)

    async def create_skill(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        name: str,
        description: str | None,
    ) -> Skill:
        self.calls.append("create_skill")
        if self._skill_name_taken(tenant_id, name):
            raise SkillNameTakenError()
        # `Skill` has no timestamps in the wire shape; the dict key carries
        # tenant scope.
        row = Skill(
            id=skill_id,
            name=name,
            description=description,
            archived_at=None,
        )
        self.skills[(tenant_id, skill_id)] = row
        return row

    async def get_skill(self, tenant_id: UUID, skill_id: UUID) -> Skill | None:
        return self.skills.get((tenant_id, skill_id))

    async def list_skills(
        self, tenant_id: UUID, filters: SkillFilters, page: PageParams
    ) -> tuple[Sequence[Skill], int]:
        needle = filters.q.lower() if filters.q else None
        rows = [
            row
            for (row_tenant, _), row in self.skills.items()
            if row_tenant == tenant_id
            and (filters.include_archived or row.archived_at is None)
            and (needle is None or needle in row.name.lower())
        ]
        rows.sort(key=lambda row: (row.name, row.id))
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

    async def update_skill(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Skill | None:
        self.calls.append("update_skill")
        key = (tenant_id, skill_id)
        row = self.skills.get(key)
        if row is None:
            return None
        name = changes.get("name", row.name)
        if self._skill_name_taken(tenant_id, name, exclude_id=skill_id):
            raise SkillNameTakenError()
        updated = row.model_copy(update=changes)
        self.skills[key] = updated
        return updated

    def _set_archived(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        archived: bool,
        now: datetime,
    ) -> SkillArchiveResult | None:
        key = (tenant_id, skill_id)
        row = self.skills.get(key)
        if row is None:
            return None
        already = (row.archived_at is not None) == archived
        if already:
            return SkillArchiveResult(skill=row, changed=False)
        updated = row.model_copy(update={"archived_at": now if archived else None})
        self.skills[key] = updated
        return SkillArchiveResult(skill=updated, changed=True)

    async def archive_skill(
        self, tenant_id: UUID, skill_id: UUID, *, now: datetime
    ) -> SkillArchiveResult | None:
        self.calls.append("archive_skill")
        if self._race_lost():
            # A concurrent archiver won: the re-read sees the row archived and
            # this call is the no-op path, not a state error.
            key = (tenant_id, skill_id)
            row = self.skills.get(key)
            if row is None:
                return None
            archived = row.model_copy(update={"archived_at": now})
            self.skills[key] = archived
            return SkillArchiveResult(skill=archived, changed=False)
        return self._set_archived(tenant_id, skill_id, archived=True, now=now)

    async def restore_skill(
        self, tenant_id: UUID, skill_id: UUID, *, now: datetime
    ) -> SkillArchiveResult | None:
        self.calls.append("restore_skill")
        if self._race_lost():
            key = (tenant_id, skill_id)
            row = self.skills.get(key)
            if row is None:
                return None
            restored = row.model_copy(update={"archived_at": None})
            self.skills[key] = restored
            return SkillArchiveResult(skill=restored, changed=False)
        return self._set_archived(tenant_id, skill_id, archived=False, now=now)

    # --- employee skills -----------------------------------------------------

    def _employee_skill_view(
        self, tenant_id: UUID, row: EmployeeSkill
    ) -> EmployeeSkill:
        """Refresh the joined label so a rename is visible immediately."""
        skill = self.skills.get((tenant_id, row.skill_id))
        if skill is None:
            return row
        return row.model_copy(update={"skill_name": skill.name})

    async def record_employee_skill(
        self,
        tenant_id: UUID,
        *,
        employee_id: UUID,
        skill_id: UUID,
        proficiency: int,
        note: str | None,
    ) -> EmployeeSkill:
        self.calls.append("record_employee_skill")
        key = (tenant_id, employee_id, skill_id)
        if key in self.employee_skills:
            raise EmployeeSkillExistsError()
        skill = self.skills.get((tenant_id, skill_id))
        if skill is None:
            raise SkillNotFoundError()
        created_at = self._stamp()
        row = EmployeeSkill(
            skill_id=skill_id,
            skill_name=skill.name,
            proficiency=proficiency,
            note=note,
            created_at=created_at,
            updated_at=created_at,
        )
        self.employee_skills[key] = row
        return row

    async def list_employee_skills(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[EmployeeSkill]:
        rows = [
            self._employee_skill_view(tenant_id, row)
            for (row_tenant, row_employee, _), row in self.employee_skills.items()
            if row_tenant == tenant_id and row_employee == employee_id
        ]
        rows.sort(key=lambda row: (row.skill_name, row.skill_id))
        return rows

    async def get_employee_skill(
        self, tenant_id: UUID, employee_id: UUID, skill_id: UUID
    ) -> EmployeeSkill | None:
        row = self.employee_skills.get((tenant_id, employee_id, skill_id))
        return None if row is None else self._employee_skill_view(tenant_id, row)

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
        self.calls.append("update_employee_skill")
        key = (tenant_id, employee_id, skill_id)
        row = self.employee_skills.get(key)
        if row is None:
            return None
        updated = row.model_copy(
            update={
                "proficiency": proficiency,
                "note": note,
                "updated_at": now,
            }
        )
        self.employee_skills[key] = updated
        return self._employee_skill_view(tenant_id, updated)

    async def remove_employee_skill(
        self, tenant_id: UUID, employee_id: UUID, skill_id: UUID
    ) -> bool:
        self.calls.append("remove_employee_skill")
        removed = self.employee_skills.pop((tenant_id, employee_id, skill_id), None)
        return removed is not None


class FakeEmployeeScope:
    """The `EmployeeScope` port: self + direct reports, one level."""

    def __init__(
        self, employees: dict[tuple[UUID, UUID], EmployeeFacts] | None = None
    ) -> None:
        # Pass `repo.employees` to share the directory between both fakes.
        self.employees = employees if employees is not None else {}

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> Sequence[UUID]:
        if manager_employee_id is None:
            return ()
        visible = [
            facts.id
            for (row_tenant, _), facts in self.employees.items()
            if row_tenant == tenant_id
            and (
                facts.id == manager_employee_id
                or facts.manager_id == manager_employee_id
            )
        ]
        return tuple(sorted(visible))
