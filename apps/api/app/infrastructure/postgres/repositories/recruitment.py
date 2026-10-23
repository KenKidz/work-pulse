"""Postgres repository for the Recruitment domain — jobs (T2), candidates (T3).

Every statement carries `WHERE tenant_id = :tenant` first — tenant scoping
lives here and nowhere else (`.ai/security.md`). Reads/writes expose the pinned
wire shape (`schemas.py`); ORM rows never leave this module.

Job and candidate transitions are conditional updates on the observed source
status and return `None` when zero rows matched, so a concurrent editor's win
surfaces as the pinned wrong-state error in the service. Visibility is derived
from interview assignments: an interviewer sees exactly the jobs and candidates
of their interviews (the join is empty until interviews exist in T5).

Candidate writes map the `(tenant_id, job_id, email)` unique violation to the
pinned `candidate_already_applied` — create and update share that guard.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.recruitment import models as orm
from app.domains.recruitment.exceptions import CandidateAlreadyAppliedError
from app.domains.recruitment.repository import CandidateFilters, JobFilters
from app.domains.recruitment.schemas import (
    Candidate,
    CandidateRejectedFrom,
    CandidateSource,
    CandidateStatus,
    Job,
    JobStatus,
)
from app.infrastructure.postgres.like import LIKE_ESCAPE, contains_pattern
from app.shared.errors import WorkPulseError
from app.shared.pagination import PageParams

# States a job may be edited from (T2 pinned transition semantics).
_EDITABLE = (JobStatus.DRAFT.value, JobStatus.OPEN.value)
# Candidate states that carry a side-exit note on the row.
_SIDE_EXITS = (CandidateStatus.REJECTED.value, CandidateStatus.WITHDRAWN.value)


def _job(row: orm.Job) -> Job:
    """ORM row → the pinned wire shape."""
    return Job.model_validate(row)


def _candidate(row: orm.Candidate) -> Candidate:
    """ORM row → the pinned wire shape."""
    return Candidate.model_validate(row)


def _map_unique_violation(exc: IntegrityError) -> WorkPulseError | None:
    """Map a candidate unique-constraint race to the pinned domain error.

    Asyncpg exposes the violated constraint/index name; the message fallback
    keeps the mapping working for wrapped drivers. Anything else is not ours to
    reinterpret — the caller re-raises it unchanged.
    """
    haystack = getattr(exc.orig, "constraint_name", None) or str(exc.orig)
    if "uq_candidates_tenant_job_email" in haystack:
        return CandidateAlreadyAppliedError()
    return None


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

    async def visible_candidate_ids(
        self, tenant_id: UUID, employee_id: UUID
    ) -> Sequence[UUID]:
        stmt = (
            select(orm.Interview.candidate_id)
            .where(
                orm.Interview.tenant_id == tenant_id,
                orm.Interview.interviewer_id == employee_id,
            )
            .distinct()
            .order_by(orm.Interview.candidate_id.asc())
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

    # --- candidates ----------------------------------------------------------

    async def create_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        job_id: UUID,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None,
        source: CandidateSource,
        notes: str | None,
    ) -> Candidate:
        row = orm.Candidate(
            id=candidate_id,
            tenant_id=tenant_id,
            job_id=job_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            source=source.value,
            notes=notes,
            status=CandidateStatus.APPLIED.value,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            error = _map_unique_violation(exc)
            if error is not None:
                raise error from exc
            raise
        await self._session.refresh(row)
        return _candidate(row)

    async def get_candidate(
        self, tenant_id: UUID, candidate_id: UUID
    ) -> Candidate | None:
        stmt = select(orm.Candidate).where(
            orm.Candidate.tenant_id == tenant_id,
            orm.Candidate.id == candidate_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _candidate(row) if row is not None else None

    async def list_candidates(
        self, tenant_id: UUID, filters: CandidateFilters, page: PageParams
    ) -> tuple[Sequence[Candidate], int]:
        if (
            filters.visible_candidate_ids is not None
            and not filters.visible_candidate_ids
        ):
            return [], 0
        conditions = [orm.Candidate.tenant_id == tenant_id]
        if filters.visible_candidate_ids is not None:
            conditions.append(orm.Candidate.id.in_(filters.visible_candidate_ids))
        if filters.job_id is not None:
            conditions.append(orm.Candidate.job_id == filters.job_id)
        if filters.status is not None:
            conditions.append(orm.Candidate.status == filters.status.value)
        if filters.q:
            pattern = contains_pattern(filters.q)
            conditions.append(
                or_(
                    orm.Candidate.first_name.ilike(pattern, escape=LIKE_ESCAPE),
                    orm.Candidate.last_name.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )

        total_items = (
            await self._session.execute(
                select(func.count()).select_from(orm.Candidate).where(*conditions)
            )
        ).scalar_one()
        rows_stmt = (
            select(orm.Candidate)
            .where(*conditions)
            .order_by(orm.Candidate.created_at.desc(), orm.Candidate.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        rows = (await self._session.execute(rows_stmt)).scalars()
        return [_candidate(row) for row in rows], total_items

    async def update_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        now: datetime,
        **changes: Any,
    ) -> Candidate | None:
        stmt = select(orm.Candidate).where(
            orm.Candidate.tenant_id == tenant_id,
            orm.Candidate.id == candidate_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = now
        try:
            await self._session.flush()
        except IntegrityError as exc:
            error = _map_unique_violation(exc)
            if error is not None:
                raise error from exc
            raise
        await self._session.refresh(row)
        return _candidate(row)

    async def transition_candidate(
        self,
        tenant_id: UUID,
        candidate_id: UUID,
        *,
        from_status: CandidateStatus,
        to_status: CandidateStatus,
        now: datetime,
        stage_note: str | None = None,
        rejected_from: CandidateRejectedFrom | None = None,
    ) -> Candidate | None:
        values: dict[str, Any] = {
            "status": to_status.value,
            "updated_at": now,
        }
        # Side exits carry the note; advances never touch it.
        if to_status.value in _SIDE_EXITS:
            values["stage_note"] = stage_note
        if rejected_from is not None:
            values["rejected_from"] = rejected_from.value
        stmt = (
            update(orm.Candidate)
            .where(
                orm.Candidate.tenant_id == tenant_id,
                orm.Candidate.id == candidate_id,
                orm.Candidate.status == from_status.value,
            )
            .values(**values)
            .returning(orm.Candidate.id)
        )
        updated = (await self._session.execute(stmt)).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_candidate(tenant_id, candidate_id)
