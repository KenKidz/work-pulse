"""Postgres repository for the Performance domain (T4).

Every statement carries `WHERE tenant_id = :tenant` first — tenant scoping
lives here and nowhere else (`.ai/security.md`). Reads/writes expose the pinned
wire shape (`schemas.py`); ORM rows never leave this module.

Goal, review and provider transitions are conditional updates on the observed
source state and return `None`/`False` when zero rows matched, so a concurrent
editor's win surfaces as the pinned wrong-state error in the service. Feedback
submission and provider replacement lock the review row (`FOR UPDATE`) so a
`complete` and a late comment/churn serialize instead of interleaving. Unique
constraint races map to the pinned `409`s inside this adapter — the service
never pre-checks a race it cannot win.

Skill archive/restore returns `changed=False` for the ADR 0007 no-op (already in
the target state), so the service can return `200` without writing an audit
entry. The catalog uniqueness spans archived rows, so restore never conflicts;
renaming a skill in use is allowed (the documented divergence from the
department rule).

`_review` always attaches the nomination list; the subject-facing/provider
shell serialization is the service's job (T6), not the repository's.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as employees_orm
from app.domains.employees.schemas import EmployeeStatus
from app.domains.identity import models as identity_orm
from app.domains.performance import models as orm
from app.domains.performance.exceptions import (
    EmployeeSkillExistsError,
    FeedbackAlreadySubmittedError,
    PerformanceDeniedError,
    SkillNameTakenError,
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
from app.infrastructure.postgres.like import LIKE_ESCAPE, contains_pattern
from app.shared.errors import WorkPulseError
from app.shared.pagination import PageParams

_EDITABLE_GOAL_STATUSES = (GoalStatus.DRAFT.value, GoalStatus.ACTIVE.value)

_EMPLOYEE_SKILL_COLUMNS = (
    orm.EmployeeSkill.skill_id,
    orm.Skill.name.label("skill_name"),
    orm.EmployeeSkill.proficiency,
    orm.EmployeeSkill.note,
    orm.EmployeeSkill.created_at,
    orm.EmployeeSkill.updated_at,
)


def _goal(row: orm.Goal) -> Goal:
    """ORM row → the pinned wire shape."""
    return Goal.model_validate(row)


def _skill(row: orm.Skill) -> Skill:
    """ORM row → the pinned wire shape."""
    return Skill.model_validate(row)


def _feedback(row: orm.ReviewFeedback) -> ReviewFeedback:
    """ORM row → the pinned wire shape."""
    return ReviewFeedback.model_validate(row)


def _review(row: orm.Review, provider_ids: Sequence[UUID]) -> Review:
    """ORM row + nominations → the pinned wire shape.

    `feedback_provider_ids` is always a list here; the service sets it to
    `None` for the subject-facing/provider shell serialization.
    """
    return Review(
        id=row.id,
        employee_id=row.employee_id,
        period=row.period,
        rating=row.rating,
        strengths=row.strengths,
        improvements=row.improvements,
        status=ReviewStatus(row.status),
        feedback_provider_ids=list(provider_ids),
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _employee_skill(row: Any) -> EmployeeSkill:
    """A joined employee-skill row → the pinned wire shape."""
    return EmployeeSkill(
        skill_id=row.skill_id,
        skill_name=row.skill_name,
        proficiency=row.proficiency,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _employee_skill_from_orm(row: orm.EmployeeSkill, skill_name: str) -> EmployeeSkill:
    """An ORM employee-skill row plus its catalog name → the wire shape."""
    return EmployeeSkill(
        skill_id=row.skill_id,
        skill_name=skill_name,
        proficiency=row.proficiency,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _map_unique_violation(exc: IntegrityError) -> WorkPulseError | None:
    """Map a performance unique-constraint race to the pinned domain error.

    Asyncpg exposes the violated constraint/index name; the message fallback
    keeps the mapping working for wrapped drivers. Anything else is not ours to
    reinterpret — the caller re-raises it unchanged.
    """
    haystack = getattr(exc.orig, "constraint_name", None) or str(exc.orig)
    if "uq_performance_skills_tenant_name" in haystack:
        return SkillNameTakenError()
    if "pk_performance_employee_skills" in haystack:
        return EmployeeSkillExistsError()
    if "uq_performance_review_feedback_review_author" in haystack:
        return FeedbackAlreadySubmittedError()
    return None


class PostgresPerformanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _flush_or_map_unique(self) -> None:
        """Flush pending rows, mapping performance unique races to 409s."""
        try:
            await self._session.flush()
        except IntegrityError as exc:
            error = _map_unique_violation(exc)
            if error is not None:
                raise error from exc
            raise

    # --- employee reads ------------------------------------------------------

    async def employee_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> EmployeeFacts | None:
        stmt = select(
            employees_orm.Employee.id,
            employees_orm.Employee.status,
            employees_orm.Employee.manager_id,
        ).where(
            employees_orm.Employee.tenant_id == tenant_id,
            employees_orm.Employee.id == employee_id,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return EmployeeFacts(
            id=row.id,
            active=EmployeeStatus(row.status) == EmployeeStatus.ACTIVE,
            manager_id=row.manager_id,
        )

    async def employee_facts_many(
        self, tenant_id: UUID, employee_ids: Sequence[UUID]
    ) -> dict[UUID, EmployeeFacts]:
        ids = list(dict.fromkeys(employee_ids))
        if not ids:
            return {}
        stmt = select(
            employees_orm.Employee.id,
            employees_orm.Employee.status,
            employees_orm.Employee.manager_id,
        ).where(
            employees_orm.Employee.tenant_id == tenant_id,
            employees_orm.Employee.id.in_(ids),
        )
        return {
            row.id: EmployeeFacts(
                id=row.id,
                active=EmployeeStatus(row.status) == EmployeeStatus.ACTIVE,
                manager_id=row.manager_id,
            )
            for row in (await self._session.execute(stmt)).all()
        }

    async def user_employee_id(self, tenant_id: UUID, user_id: UUID) -> UUID | None:
        stmt = select(identity_orm.User.employee_id).where(
            identity_orm.User.tenant_id == tenant_id,
            identity_orm.User.id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

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
        row = orm.Goal(
            id=goal_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            title=title,
            description=description,
            due_date=due_date,
            progress_pct=progress_pct,
            status=GoalStatus.DRAFT.value,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _goal(row)

    async def get_goal(self, tenant_id: UUID, goal_id: UUID) -> Goal | None:
        stmt = select(orm.Goal).where(
            orm.Goal.tenant_id == tenant_id,
            orm.Goal.id == goal_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _goal(row) if row is not None else None

    async def list_goals(
        self, tenant_id: UUID, filters: GoalFilters, page: PageParams
    ) -> tuple[Sequence[Goal], int]:
        if (
            filters.visible_employee_ids is not None
            and not filters.visible_employee_ids
        ):
            return [], 0
        conditions = [orm.Goal.tenant_id == tenant_id]
        if filters.visible_employee_ids is not None:
            conditions.append(orm.Goal.employee_id.in_(filters.visible_employee_ids))
        if filters.employee_id is not None:
            conditions.append(orm.Goal.employee_id == filters.employee_id)
        if filters.status is not None:
            conditions.append(orm.Goal.status == filters.status.value)

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.Goal).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.Goal)
            .where(*conditions)
            .order_by(orm.Goal.created_at.desc(), orm.Goal.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).scalars()
        return [_goal(row) for row in rows], total_items

    async def update_goal(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Goal | None:
        stmt = (
            update(orm.Goal)
            .where(
                orm.Goal.tenant_id == tenant_id,
                orm.Goal.id == goal_id,
                orm.Goal.status.in_(_EDITABLE_GOAL_STATUSES),
            )
            .values(**changes, updated_at=now)
            .returning(orm.Goal.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_goal(tenant_id, goal_id)

    async def transition_goal_activate(
        self, tenant_id: UUID, goal_id: UUID, *, now: datetime
    ) -> Goal | None:
        stmt = (
            update(orm.Goal)
            .where(
                orm.Goal.tenant_id == tenant_id,
                orm.Goal.id == goal_id,
                orm.Goal.status == GoalStatus.DRAFT.value,
            )
            .values(
                status=GoalStatus.ACTIVE.value,
                updated_at=now,
            )
            .returning(orm.Goal.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_goal(tenant_id, goal_id)

    async def transition_goal_complete(
        self, tenant_id: UUID, goal_id: UUID, *, now: datetime
    ) -> Goal | None:
        stmt = (
            update(orm.Goal)
            .where(
                orm.Goal.tenant_id == tenant_id,
                orm.Goal.id == goal_id,
                orm.Goal.status == GoalStatus.ACTIVE.value,
            )
            .values(
                status=GoalStatus.COMPLETED.value,
                completed_at=now,
                updated_at=now,
            )
            .returning(orm.Goal.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_goal(tenant_id, goal_id)

    async def transition_goal_cancel(
        self,
        tenant_id: UUID,
        goal_id: UUID,
        *,
        from_status: GoalStatus,
        now: datetime,
    ) -> Goal | None:
        stmt = (
            update(orm.Goal)
            .where(
                orm.Goal.tenant_id == tenant_id,
                orm.Goal.id == goal_id,
                orm.Goal.status == from_status.value,
            )
            .values(
                status=GoalStatus.CANCELLED.value,
                updated_at=now,
            )
            .returning(orm.Goal.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_goal(tenant_id, goal_id)

    # --- reviews -------------------------------------------------------------

    async def _provider_map(
        self, tenant_id: UUID, review_ids: Sequence[UUID]
    ) -> dict[UUID, list[UUID]]:
        """Nomination ids per review, keyed for response serialization."""
        if not review_ids:
            return {}
        stmt = (
            select(orm.ReviewProvider.review_id, orm.ReviewProvider.employee_id)
            .where(
                orm.ReviewProvider.tenant_id == tenant_id,
                orm.ReviewProvider.review_id.in_(review_ids),
            )
            .order_by(
                orm.ReviewProvider.review_id.asc(),
                orm.ReviewProvider.employee_id.asc(),
            )
        )
        providers: dict[UUID, list[UUID]] = {review_id: [] for review_id in review_ids}
        for row in (await self._session.execute(stmt)).all():
            providers.setdefault(row.review_id, []).append(row.employee_id)
        return providers

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
        provider_ids = list(dict.fromkeys(feedback_provider_ids))
        row = orm.Review(
            id=review_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            author_id=author_id,
            period=period,
            rating=rating,
            strengths=strengths,
            improvements=improvements,
            status=ReviewStatus.DRAFT.value,
        )
        self._session.add(row)
        for provider_id in provider_ids:
            self._session.add(
                orm.ReviewProvider(
                    review_id=review_id,
                    employee_id=provider_id,
                    tenant_id=tenant_id,
                )
            )
        await self._session.flush()
        await self._session.refresh(row)
        return _review(row, provider_ids)

    async def _review_orm_row(
        self, tenant_id: UUID, review_id: UUID
    ) -> orm.Review | None:
        stmt = select(orm.Review).where(
            orm.Review.tenant_id == tenant_id,
            orm.Review.id == review_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_review(self, tenant_id: UUID, review_id: UUID) -> Review | None:
        row = await self._review_orm_row(tenant_id, review_id)
        if row is None:
            return None
        providers = await self._provider_map(tenant_id, [review_id])
        return _review(row, providers.get(review_id, []))

    async def get_review_with_author(
        self, tenant_id: UUID, review_id: UUID
    ) -> ReviewRecord | None:
        row = await self._review_orm_row(tenant_id, review_id)
        if row is None:
            return None
        providers = await self._provider_map(tenant_id, [review_id])
        return ReviewRecord(
            review=_review(row, providers.get(review_id, [])),
            author_id=row.author_id,
        )

    async def list_reviews(
        self, tenant_id: UUID, filters: ReviewFilters, page: PageParams
    ) -> tuple[Sequence[Review], int]:
        if filters.feedback_provider_id is not None:
            conditions = [
                orm.Review.tenant_id == tenant_id,
                orm.Review.status == ReviewStatus.DRAFT.value,
                orm.Review.id.in_(
                    select(orm.ReviewProvider.review_id).where(
                        orm.ReviewProvider.tenant_id == tenant_id,
                        orm.ReviewProvider.employee_id == filters.feedback_provider_id,
                    )
                ),
            ]
        else:
            conditions = [orm.Review.tenant_id == tenant_id]
            grants = []
            if filters.visible_employee_ids is not None:
                if filters.visible_employee_ids:
                    grants.append(
                        orm.Review.employee_id.in_(filters.visible_employee_ids)
                    )
            if filters.completed_only_employee_ids:
                # The manager's own rows: completed only, so a draft review
                # about the manager never reaches their surface.
                grants.append(
                    and_(
                        orm.Review.employee_id.in_(filters.completed_only_employee_ids),
                        orm.Review.status == ReviewStatus.COMPLETED.value,
                    )
                )
            if filters.author_id is not None:
                grants.append(orm.Review.author_id == filters.author_id)
            if (
                filters.visible_employee_ids is not None
                or filters.completed_only_employee_ids is not None
                or filters.author_id is not None
            ):
                if not grants:
                    return [], 0
                conditions.append(or_(*grants))

        if filters.hidden_non_completed_employee_ids:
            # Draft/terminal invisibility for every role, including a
            # management-role subject whose list is otherwise unscoped.
            conditions.append(
                ~and_(
                    orm.Review.employee_id.in_(
                        filters.hidden_non_completed_employee_ids
                    ),
                    orm.Review.status != ReviewStatus.COMPLETED.value,
                )
            )
        if filters.employee_id is not None:
            conditions.append(orm.Review.employee_id == filters.employee_id)
        if filters.status is not None:
            conditions.append(orm.Review.status == filters.status.value)

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.Review).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.Review)
            .where(*conditions)
            .order_by(orm.Review.created_at.desc(), orm.Review.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = list((await self._session.execute(rows_stmt)).scalars())
        providers = await self._provider_map(tenant_id, [row.id for row in rows])
        return (
            [_review(row, providers.get(row.id, [])) for row in rows],
            total_items,
        )

    async def update_review(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Review | None:
        stmt = (
            update(orm.Review)
            .where(
                orm.Review.tenant_id == tenant_id,
                orm.Review.id == review_id,
                orm.Review.status == ReviewStatus.DRAFT.value,
            )
            .values(**changes, updated_at=now)
            .returning(orm.Review.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_review(tenant_id, review_id)

    async def replace_review_providers(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        employee_ids: Sequence[UUID],
        now: datetime,
    ) -> bool:
        lock_stmt = (
            select(orm.Review.id, orm.Review.status)
            .where(
                orm.Review.tenant_id == tenant_id,
                orm.Review.id == review_id,
            )
            .with_for_update()
        )
        locked = (await self._session.execute(lock_stmt)).one_or_none()
        if locked is None or locked.status != ReviewStatus.DRAFT.value:
            return False

        wanted = list(dict.fromkeys(employee_ids))
        wanted_set = set(wanted)
        current = set(
            (
                await self._session.execute(
                    select(orm.ReviewProvider.employee_id).where(
                        orm.ReviewProvider.tenant_id == tenant_id,
                        orm.ReviewProvider.review_id == review_id,
                    )
                )
            ).scalars()
        )
        removed = current - wanted_set
        if removed:
            # Re-check under the row lock: a comment that committed after the
            # service's pre-read still stands, so its provider cannot be
            # removed (pinned `409 feedback_already_submitted`).
            filed = set(
                (
                    await self._session.execute(
                        select(orm.ReviewFeedback.author_id).where(
                            orm.ReviewFeedback.tenant_id == tenant_id,
                            orm.ReviewFeedback.review_id == review_id,
                            orm.ReviewFeedback.author_id.in_(removed),
                        )
                    )
                ).scalars()
            )
            if filed:
                raise FeedbackAlreadySubmittedError()
            await self._session.execute(
                delete(orm.ReviewProvider).where(
                    orm.ReviewProvider.tenant_id == tenant_id,
                    orm.ReviewProvider.review_id == review_id,
                    orm.ReviewProvider.employee_id.in_(removed),
                )
            )
        for employee_id in wanted:
            if employee_id not in current:
                self._session.add(
                    orm.ReviewProvider(
                        review_id=review_id,
                        employee_id=employee_id,
                        tenant_id=tenant_id,
                    )
                )
        await self._session.execute(
            update(orm.Review)
            .where(
                orm.Review.tenant_id == tenant_id,
                orm.Review.id == review_id,
            )
            .values(updated_at=now)
        )
        await self._session.flush()
        return True

    async def feedback_author_ids(
        self, tenant_id: UUID, review_id: UUID
    ) -> Sequence[UUID]:
        stmt = (
            select(orm.ReviewFeedback.author_id)
            .where(
                orm.ReviewFeedback.tenant_id == tenant_id,
                orm.ReviewFeedback.review_id == review_id,
            )
            .order_by(orm.ReviewFeedback.author_id.asc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def transition_review_complete(
        self, tenant_id: UUID, review_id: UUID, *, now: datetime
    ) -> Review | None:
        stmt = (
            update(orm.Review)
            .where(
                orm.Review.tenant_id == tenant_id,
                orm.Review.id == review_id,
                orm.Review.status == ReviewStatus.DRAFT.value,
                orm.Review.rating.is_not(None),
            )
            .values(
                status=ReviewStatus.COMPLETED.value,
                completed_at=now,
                updated_at=now,
            )
            .returning(orm.Review.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_review(tenant_id, review_id)

    async def transition_review_cancel(
        self, tenant_id: UUID, review_id: UUID, *, now: datetime
    ) -> Review | None:
        stmt = (
            update(orm.Review)
            .where(
                orm.Review.tenant_id == tenant_id,
                orm.Review.id == review_id,
                orm.Review.status == ReviewStatus.DRAFT.value,
            )
            .values(
                status=ReviewStatus.CANCELLED.value,
                updated_at=now,
            )
            .returning(orm.Review.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_review(tenant_id, review_id)

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
        lock_stmt = (
            select(orm.Review.status, orm.Review.employee_id)
            .where(
                orm.Review.tenant_id == tenant_id,
                orm.Review.id == review_id,
            )
            .with_for_update()
        )
        locked = (await self._session.execute(lock_stmt)).one_or_none()
        if locked is None or locked.status != ReviewStatus.DRAFT.value:
            return None
        if locked.employee_id == author_id:
            raise PerformanceDeniedError()

        # Re-check the nomination under the same lock: a concurrent provider
        # replacement must not leave a removed employee able to submit.
        nomination = (
            await self._session.execute(
                select(orm.ReviewProvider.employee_id).where(
                    orm.ReviewProvider.tenant_id == tenant_id,
                    orm.ReviewProvider.review_id == review_id,
                    orm.ReviewProvider.employee_id == author_id,
                )
            )
        ).scalar_one_or_none()
        if nomination is None:
            raise PerformanceDeniedError()

        row = orm.ReviewFeedback(
            id=feedback_id,
            tenant_id=tenant_id,
            review_id=review_id,
            author_id=author_id,
            comment=comment,
            created_at=now,
        )
        self._session.add(row)
        await self._flush_or_map_unique()
        await self._session.refresh(row)
        return _feedback(row)

    async def list_feedback(
        self,
        tenant_id: UUID,
        review_id: UUID,
        *,
        author_id: UUID | None = None,
    ) -> Sequence[ReviewFeedback]:
        conditions = [
            orm.ReviewFeedback.tenant_id == tenant_id,
            orm.ReviewFeedback.review_id == review_id,
        ]
        if author_id is not None:
            conditions.append(orm.ReviewFeedback.author_id == author_id)
        stmt = (
            select(orm.ReviewFeedback)
            .where(*conditions)
            .order_by(
                orm.ReviewFeedback.created_at.asc(),
                orm.ReviewFeedback.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_feedback(row) for row in rows]

    # --- skill catalog -------------------------------------------------------

    async def create_skill(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        name: str,
        description: str | None,
    ) -> Skill:
        row = orm.Skill(
            id=skill_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
        )
        self._session.add(row)
        await self._flush_or_map_unique()
        await self._session.refresh(row)
        return _skill(row)

    async def get_skill(self, tenant_id: UUID, skill_id: UUID) -> Skill | None:
        stmt = select(orm.Skill).where(
            orm.Skill.tenant_id == tenant_id,
            orm.Skill.id == skill_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _skill(row) if row is not None else None

    async def list_skills(
        self, tenant_id: UUID, filters: SkillFilters, page: PageParams
    ) -> tuple[Sequence[Skill], int]:
        conditions = [orm.Skill.tenant_id == tenant_id]
        if not filters.include_archived:
            conditions.append(orm.Skill.archived_at.is_(None))
        if filters.q:
            conditions.append(
                orm.Skill.name.ilike(contains_pattern(filters.q), escape=LIKE_ESCAPE)
            )

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.Skill).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.Skill)
            .where(*conditions)
            .order_by(orm.Skill.name.asc(), orm.Skill.id.asc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).scalars()
        return [_skill(row) for row in rows], total_items

    async def update_skill(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Skill | None:
        stmt = (
            update(orm.Skill)
            .where(
                orm.Skill.tenant_id == tenant_id,
                orm.Skill.id == skill_id,
            )
            .values(**changes, updated_at=now)
            .returning(orm.Skill.id)
        )
        try:
            updated = (await self._session.execute(stmt)).scalar_one_or_none()
        except IntegrityError as exc:
            error = _map_unique_violation(exc)
            if error is not None:
                raise error from exc
            raise
        if updated is None:
            return None
        return await self.get_skill(tenant_id, skill_id)

    async def _set_archived(
        self,
        tenant_id: UUID,
        skill_id: UUID,
        *,
        archived: bool,
        now: datetime,
    ) -> SkillArchiveResult | None:
        state_predicate = (
            orm.Skill.archived_at.is_(None)
            if archived
            else orm.Skill.archived_at.is_not(None)
        )
        stmt = (
            update(orm.Skill)
            .where(
                orm.Skill.tenant_id == tenant_id,
                orm.Skill.id == skill_id,
                state_predicate,
            )
            .values(
                archived_at=now if archived else None,
                updated_at=now,
            )
            .returning(orm.Skill.id)
        )
        changed = (await self._session.execute(stmt)).scalar_one_or_none() is not None
        skill = await self.get_skill(tenant_id, skill_id)
        if skill is None:
            return None
        return SkillArchiveResult(skill=skill, changed=changed)

    async def archive_skill(
        self, tenant_id: UUID, skill_id: UUID, *, now: datetime
    ) -> SkillArchiveResult | None:
        return await self._set_archived(tenant_id, skill_id, archived=True, now=now)

    async def restore_skill(
        self, tenant_id: UUID, skill_id: UUID, *, now: datetime
    ) -> SkillArchiveResult | None:
        return await self._set_archived(tenant_id, skill_id, archived=False, now=now)

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
        row = orm.EmployeeSkill(
            tenant_id=tenant_id,
            employee_id=employee_id,
            skill_id=skill_id,
            proficiency=proficiency,
            note=note,
        )
        self._session.add(row)
        await self._flush_or_map_unique()
        await self._session.refresh(row)
        skill_name = (
            await self._session.execute(
                select(orm.Skill.name).where(
                    orm.Skill.tenant_id == tenant_id,
                    orm.Skill.id == skill_id,
                )
            )
        ).scalar_one()
        return _employee_skill_from_orm(row, skill_name)

    async def list_employee_skills(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[EmployeeSkill]:
        stmt = (
            select(*_EMPLOYEE_SKILL_COLUMNS)
            .join(orm.Skill, orm.Skill.id == orm.EmployeeSkill.skill_id)
            .where(
                orm.EmployeeSkill.tenant_id == tenant_id,
                orm.EmployeeSkill.employee_id == employee_id,
            )
            .order_by(orm.Skill.name.asc(), orm.EmployeeSkill.skill_id.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [_employee_skill(row) for row in rows]

    async def get_employee_skill(
        self, tenant_id: UUID, employee_id: UUID, skill_id: UUID
    ) -> EmployeeSkill | None:
        stmt = (
            select(*_EMPLOYEE_SKILL_COLUMNS)
            .join(orm.Skill, orm.Skill.id == orm.EmployeeSkill.skill_id)
            .where(
                orm.EmployeeSkill.tenant_id == tenant_id,
                orm.EmployeeSkill.employee_id == employee_id,
                orm.EmployeeSkill.skill_id == skill_id,
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        return _employee_skill(row) if row is not None else None

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
        stmt = (
            update(orm.EmployeeSkill)
            .where(
                orm.EmployeeSkill.tenant_id == tenant_id,
                orm.EmployeeSkill.employee_id == employee_id,
                orm.EmployeeSkill.skill_id == skill_id,
            )
            .values(
                proficiency=proficiency,
                note=note,
                updated_at=now,
            )
            .returning(orm.EmployeeSkill.skill_id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_employee_skill(tenant_id, employee_id, skill_id)

    async def remove_employee_skill(
        self, tenant_id: UUID, employee_id: UUID, skill_id: UUID
    ) -> bool:
        stmt = (
            delete(orm.EmployeeSkill)
            .where(
                orm.EmployeeSkill.tenant_id == tenant_id,
                orm.EmployeeSkill.employee_id == employee_id,
                orm.EmployeeSkill.skill_id == skill_id,
            )
            .returning(orm.EmployeeSkill.skill_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None
