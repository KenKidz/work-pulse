"""Postgres repository for the Recruitment domain — jobs (T2).

Every statement carries `WHERE tenant_id = :tenant` first — tenant scoping
lives here and nowhere else (`.ai/security.md`). Reads/writes expose the pinned
wire shape (`schemas.py`); ORM rows never leave this module.

Job transitions are conditional updates on the observed source status and
return `None` when zero rows matched, so a concurrent editor's win surfaces as
the pinned wrong-state error in the service. Visibility is derived from
interview assignments: an interviewer sees exactly the jobs of candidates they
have an interview for (the join is empty until interviews exist in T5).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.recruitment import models as orm
from app.domains.recruitment.repository import JobFilters
from app.domains.recruitment.schemas import Job, JobStatus
from app.infrastructure.postgres.like import LIKE_ESCAPE, contains_pattern
from app.shared.pagination import PageParams

# States a job may be edited from (T2 pinned transition semantics).
_EDITABLE = (JobStatus.DRAFT.value, JobStatus.OPEN.value)


def _job(row: orm.Job) -> Job:
    """ORM row → the pinned wire shape."""
    return Job.model_validate(row)


class PostgresRecruitmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- visibility ----------------------------------------------------------

    async def visible_job_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        stmt = (
            select(orm.Interview.job_id)
            .where(
                orm.Interview.tenant_id == tenant_id,
                orm.Interview.interviewer_id == employee_id,
            )
            .distinct()
            .order_by(orm.Interview.job_id.asc())
        )
        return list((await self._session.execute(stmt)).scalars())

    # --- jobs ----------------------------------------------------------------

    async def create_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        title: str,
        description: str | None,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
        openings: int,
        created_by: UUID,
    ) -> Job:
        row = orm.Job(
            id=job_id,
            tenant_id=tenant_id,
            title=title,
            description=description,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            openings=openings,
            status=JobStatus.DRAFT.value,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _job(row)

    async def get_job(self, tenant_id: UUID, job_id: UUID) -> Job | None:
        stmt = select(orm.Job).where(
            orm.Job.tenant_id == tenant_id,
            orm.Job.id == job_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _job(row) if row is not None else None

    async def list_jobs(
        self, tenant_id: UUID, filters: JobFilters, page: PageParams
    ) -> tuple[Sequence[Job], int]:
        if filters.visible_job_ids is not None and not filters.visible_job_ids:
            return [], 0
        conditions = [orm.Job.tenant_id == tenant_id]
        if filters.visible_job_ids is not None:
            conditions.append(orm.Job.id.in_(filters.visible_job_ids))
        if filters.status is not None:
            conditions.append(orm.Job.status == filters.status.value)
        if filters.department_id is not None:
            conditions.append(orm.Job.department_id == filters.department_id)
        if filters.q:
            conditions.append(
                orm.Job.title.ilike(contains_pattern(filters.q), escape=LIKE_ESCAPE)
            )

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.Job).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.Job)
            .where(*conditions)
            .order_by(orm.Job.created_at.desc(), orm.Job.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).scalars()
        return [_job(row) for row in rows], total_items

    async def update_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Job | None:
        stmt = (
            update(orm.Job)
            .where(
                orm.Job.tenant_id == tenant_id,
                orm.Job.id == job_id,
                orm.Job.status.in_(_EDITABLE),
            )
            .values(**changes, updated_at=now)
            .returning(orm.Job.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_job(tenant_id, job_id)

    async def transition_open(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> Job | None:
        stmt = (
            update(orm.Job)
            .where(
                orm.Job.tenant_id == tenant_id,
                orm.Job.id == job_id,
                orm.Job.status == JobStatus.DRAFT.value,
            )
            .values(
                status=JobStatus.OPEN.value,
                opened_at=now,
                updated_at=now,
            )
            .returning(orm.Job.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_job(tenant_id, job_id)

    async def transition_close(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> Job | None:
        stmt = (
            update(orm.Job)
            .where(
                orm.Job.tenant_id == tenant_id,
                orm.Job.id == job_id,
                orm.Job.status == JobStatus.OPEN.value,
            )
            .values(
                status=JobStatus.CLOSED.value,
                closed_at=now,
                updated_at=now,
            )
            .returning(orm.Job.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_job(tenant_id, job_id)
