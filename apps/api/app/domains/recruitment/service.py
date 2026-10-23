"""Use cases for the Recruitment domain — jobs (T2).

Rules implemented here (pinned in `.ai/features/recruitment.md`):

- **Job state machine**: created `DRAFT`; `open` moves `DRAFT → OPEN` and stamps
  `opened_at`; `close` moves `OPEN → CLOSED` and stamps `closed_at`; `CLOSED`
  is terminal. Edits are allowed while `DRAFT`/`OPEN` only. Every transition is
  an event + an audit entry appended in the mutation's transaction (ADR 0012);
  creating a draft emits nothing (the catalog's first job event is `job.opened`).
- **Org references**: `department_id`/`position_id`/`location_id` are validated
  through the Organizations services — never their tables (AGENTS.md rule 3).
  Their 404s propagate unchanged; archived refs are accepted at job creation
  (risk 4 in the spec) and refused later by Employees at hire time.
- **Access**: management mutations are HR/Admin/Super Admin only
  (`recruitment_denied` 403). Reads: management sees everything, an assigned
  interviewer sees the jobs of their interview candidates, everyone else gets
  `403`; an out-of-scope entity id is `404` (existence is not leaked).
- **Clock**: every mutation takes `now` as its last parameter — the router is
  the only wall-clock reader, and tests inject a fixed instant.
- **PII**: jobs carry no candidate PII. Candidate fields never appear in this
  module's events or audit values.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.recruitment import events
from app.domains.recruitment.exceptions import (
    JobNotDraftError,
    JobNotFoundError,
    JobNotOpenError,
)
from app.domains.recruitment.policies import (
    ensure_can_manage,
    ensure_has_standing,
    is_management,
)
from app.domains.recruitment.repository import JobFilters, RecruitmentRepository
from app.domains.recruitment.schemas import (
    Job,
    JobCreate,
    JobListPage,
    JobStatus,
    JobUpdate,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams


class OrgReferenceReader(Protocol):
    """The Organizations read used to validate job refs (service-level seam)."""

    async def get(self, identity: Identity, reference_id: UUID, /) -> object: ...


def _value(value: object) -> object:
    """Audit-safe JSON value: UUIDs become strings, everything else is as-is."""
    return str(value) if isinstance(value, UUID) else value


class RecruitmentService:
    def __init__(
        self,
        repo: RecruitmentRepository,
        audit: AuditWriter,
        outbox: EventOutbox,
        departments: OrgReferenceReader,
        positions: OrgReferenceReader,
        locations: OrgReferenceReader,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._outbox = outbox
        self._departments = departments
        self._positions = positions
        self._locations = locations

    # --- mutations -----------------------------------------------------------

    async def create_job(
        self, identity: Identity, payload: JobCreate, now: datetime
    ) -> Job:
        """Create a job in `draft` — no event until it is opened."""
        ensure_can_manage(identity)
        await self._ensure_org_refs(
            identity,
            payload.department_id,
            payload.position_id,
            payload.location_id,
        )

        job = await self._repo.create_job(
            identity.tenant_id,
            uuid7(),
            title=payload.title,
            description=payload.description,
            department_id=payload.department_id,
            position_id=payload.position_id,
            location_id=payload.location_id,
            openings=payload.openings,
            created_by=identity.actor_id,
        )

        changes: dict[str, Any] = {
            "title": {"old": None, "new": job.title},
            "department_id": {"old": None, "new": str(job.department_id)},
            "position_id": {"old": None, "new": str(job.position_id)},
            "location_id": {"old": None, "new": str(job.location_id)},
            "openings": {"old": None, "new": job.openings},
            "status": {"old": None, "new": job.status.value},
        }
        if job.description is not None:
            changes["description"] = {"old": None, "new": job.description}
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.created",
                entity_type="job",
                entity_id=job.id,
                occurred_at=now,
                changes=changes,
            )
        )
        return job

    async def update_job(
        self,
        identity: Identity,
        job_id: UUID,
        payload: JobUpdate,
        now: datetime,
    ) -> Job:
        """Edit a `draft`/`open` job; a closed job is `409 job_not_open`."""
        ensure_can_manage(identity)
        existing = await self._get_manageable(identity, job_id)
        if existing.status is JobStatus.CLOSED:
            raise JobNotOpenError()

        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return existing

        # Only the refs actually supplied are re-validated (Organizations 404s
        # propagate unchanged).
        if "department_id" in changes:
            await self._departments.get(identity, changes["department_id"])
        if "position_id" in changes:
            await self._positions.get(identity, changes["position_id"])
        if "location_id" in changes:
            await self._locations.get(identity, changes["location_id"])

        updated = await self._repo.update_job(
            identity.tenant_id, job_id, now=now, **changes
        )
        if updated is None:
            # The job was closed between the read and the conditional update.
            raise JobNotOpenError()

        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.updated",
                entity_type="job",
                entity_id=job_id,
                occurred_at=now,
                changes={
                    field: {
                        "old": _value(getattr(existing, field)),
                        "new": _value(getattr(updated, field)),
                    }
                    for field in changes
                },
            )
        )
        return updated

    async def open_job(self, identity: Identity, job_id: UUID, now: datetime) -> Job:
        """Open a draft job: `draft → open` (`409 job_not_draft` otherwise)."""
        ensure_can_manage(identity)
        existing = await self._get_manageable(identity, job_id)
        if existing.status is not JobStatus.DRAFT:
            raise JobNotDraftError()

        updated = await self._repo.transition_open(identity.tenant_id, job_id, now=now)
        if updated is None:
            raise JobNotDraftError()

        await self._outbox.append(
            events.job_opened(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                job_id=updated.id,
                position_id=updated.position_id,
                department_id=updated.department_id,
                location_id=updated.location_id,
                openings=updated.openings,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.opened",
                entity_type="job",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": JobStatus.DRAFT.value,
                        "new": JobStatus.OPEN.value,
                    }
                },
            )
        )
        return updated

    async def close_job(self, identity: Identity, job_id: UUID, now: datetime) -> Job:
        """Close an open job: `open → closed`, terminal (`409 job_not_open`)."""
        ensure_can_manage(identity)
        existing = await self._get_manageable(identity, job_id)
        if existing.status is not JobStatus.OPEN:
            raise JobNotOpenError()

        updated = await self._repo.transition_close(identity.tenant_id, job_id, now=now)
        if updated is None:
            raise JobNotOpenError()

        await self._outbox.append(
            events.job_closed(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                job_id=updated.id,
                closed_by=identity.actor_id,
            )
        )
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action="job.closed",
                entity_type="job",
                entity_id=updated.id,
                occurred_at=now,
                changes={
                    "status": {
                        "old": JobStatus.OPEN.value,
                        "new": JobStatus.CLOSED.value,
                    }
                },
            )
        )
        return updated

    # --- reads ---------------------------------------------------------------

    async def list_jobs(
        self,
        identity: Identity,
        *,
        status: JobStatus | None,
        q: str | None,
        department_id: UUID | None,
        page: PageParams,
    ) -> JobListPage:
        """Paged jobs scoped to the caller's visibility (## Roles & access)."""
        scope = await self._read_scope(identity)
        filters = JobFilters(
            status=status,
            q=q,
            department_id=department_id,
            visible_job_ids=scope,
        )
        items, total = await self._repo.list_jobs(identity.tenant_id, filters, page)
        return JobListPage(items=list(items), page=page.make_page(total))

    async def get_job(self, identity: Identity, job_id: UUID) -> Job:
        """Job detail; unknown, cross-tenant and out-of-scope ids are `404`."""
        job = await self._repo.get_job(identity.tenant_id, job_id)
        if job is None:
            raise JobNotFoundError()
        scope = await self._read_scope(identity)
        if scope is not None and job.id not in scope:
            raise JobNotFoundError()
        return job

    # --- internals -----------------------------------------------------------

    async def _get_manageable(self, identity: Identity, job_id: UUID) -> Job:
        """Fetch a job for a management mutation; missing/foreign is `404`."""
        job = await self._repo.get_job(identity.tenant_id, job_id)
        if job is None:
            raise JobNotFoundError()
        return job

    async def _read_scope(self, identity: Identity) -> Sequence[UUID] | None:
        """Effective job scope; `None` means unscoped (hr/admin/super_admin)."""
        ensure_has_standing(identity)
        if is_management(identity):
            return None
        if identity.employee_id is None:
            return []
        return await self._repo.visible_job_ids(
            identity.tenant_id, identity.employee_id
        )

    async def _ensure_org_refs(
        self,
        identity: Identity,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
    ) -> None:
        """Refs must exist in the tenant (Organizations 404s propagate)."""
        await self._departments.get(identity, department_id)
        await self._positions.get(identity, position_id)
        await self._locations.get(identity, location_id)
