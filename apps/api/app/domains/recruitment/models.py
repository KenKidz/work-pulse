"""SQLAlchemy models for the Recruitment domain (Phase 4+, T2).

Three aggregates, created together because the pipeline is one flow:

- `jobs` — a hiring opening on one Organization department/position/location,
  moving `draft → open → closed` (terminal). `openings` is the capacity the
  hire flow counts against (T4).
- `candidates` — one application on one job, moving through the pinned
  `applied → interviewing → offered → hired` pipeline with `rejected`/
  `withdrawn` terminal side exits. `(tenant_id, job_id, email)` is unique: the
  same person re-applying to the same job is refused, a different job is a new
  row. `employee_id` is set by the hire seam (ADR 0020); no FK is needed for
  the seam, but the employee row is in the same database and the same
  transaction, so the reference is relational.
- `interviews` — a scheduled conversation with one employee, completed by that
  interviewer's feedback or cancelled. `job_id` is denormalized from the
  candidate so the scoped list and the projection can filter without a join
  through candidates.

PII: candidate names, email, phone, notes, stage notes, interview notes and
feedback text are sensitive (`.ai/security.md`) — stored as data only, never
logged, emitted in events or written into audit `changes` values.

Statuses are lowercase stored strings with DB check constraints (the same
vocabulary the wire schemas pin). Indexes are tenant-first and declared in both
the model and migration 0015 (parity rule). The interview indexes cover the
`?mine=1` / interviewer-scoped reads and the interview-derived visibility the
job/candidate reads use.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class Job(Base):
    """A hiring opening: `draft → open → closed` (terminal)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("openings >= 1", name="openings_positive"),
        CheckConstraint(
            "status IN ('draft', 'open', 'closed')",
            name="status_valid",
        ),
        # Job list: status filter + newest-first ordering.
        Index("ix_jobs_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_jobs_tenant_department", "tenant_id", "department_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    position_id: Mapped[UUID] = mapped_column(
        ForeignKey("positions.id"), nullable=False
    )
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False
    )
    openings: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # The acting HR user's id (ADR 0010) — audit/event semantics, no FK.
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Candidate(Base):
    """One application on one job and its HR-driven pipeline state."""

    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('applied', 'interviewing', 'offered', 'hired', "
            "'rejected', 'withdrawn')",
            name="status_valid",
        ),
        CheckConstraint(
            "source IN ('referral', 'job_board', 'agency', 'other')",
            name="source_valid",
        ),
        CheckConstraint(
            "rejected_from IS NULL OR rejected_from IN "
            "('applied', 'interviewing', 'offered')",
            name="rejected_from_valid",
        ),
        # One application per person per job (email is exact — risk 5 in the
        # spec; the service maps the IntegrityError to `candidate_already_applied`).
        UniqueConstraint(
            "tenant_id",
            "job_id",
            "email",
            name="uq_candidates_tenant_job_email",
        ),
        # Candidate list: job + status filters, newest first.
        Index(
            "ix_candidates_tenant_job_status",
            "tenant_id",
            "job_id",
            "status",
        ),
        Index(
            "ix_candidates_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
        # Newest-first scans without a status equality: the default list and a
        # job's pipeline (migration 0016 keeps model/migration parity).
        Index(
            "ix_candidates_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_candidates_tenant_job_created",
            "tenant_id",
            "job_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    first_name: Mapped[str] = mapped_column(String(200), nullable=False)
    last_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # PII: the last reject/withdraw note and the observed source state.
    stage_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rejected_from: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set by the Employees hire seam in the same transaction (ADR 0020).
    employee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Interview(Base):
    """One scheduled conversation: `scheduled → completed | cancelled`."""

    __tablename__ = "interviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="rating_valid",
        ),
        # Candidate pipeline views (all interviews for one candidate).
        Index(
            "ix_interviews_tenant_candidate_status",
            "tenant_id",
            "candidate_id",
            "status",
        ),
        # `?mine=1` / interviewer-scoped reads and the visibility join.
        Index(
            "ix_interviews_tenant_interviewer_scheduled",
            "tenant_id",
            "interviewer_id",
            "scheduled_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidates.id"), nullable=False
    )
    # Denormalized from the candidate for filter reads without a join.
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    # The assigned interviewer's employee id (any active employee).
    interviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # PII — scheduling context and the interviewer's feedback.
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
