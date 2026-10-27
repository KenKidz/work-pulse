"""SQLAlchemy models for the Performance domain (Phase 4+, T2).

Six tables, two workflow aggregates and a reference-data catalog:

- `performance_goals` — one employee-owned goal moving
  `draft → active → completed` with `cancelled` as a terminal side exit.
  `progress_pct` is a field update, not a state transition (0..100).
- `performance_reviews` — one evaluation of one employee by one manager/HR
  user, moving `draft → completed | cancelled`. `author_id` stores the acting
  **user** id (ADR 0010 — HR/Admin seeds have no employee link); the subject is
  `employee_id`.
- `performance_review_providers` — the feedback nominations on a review.
  Composite PK `(review_id, employee_id)` so `feedback_for_me` is a join, not a
  JSON scan; the wire schema still exposes `feedback_provider_ids`.
- `performance_review_feedback` — one immutable comment per nominated provider
  per review. `author_id` here is the provider's **employee** id so it matches
  the nomination (not the user id used by reviews).
- `performance_skills` — the per-tenant skill catalog under ADR 0007 soft-delete
  semantics: `archived_at` marks archived rows and name uniqueness spans all
  rows, archived included.
- `performance_employee_skills` — one proficiency/note per `(employee, skill)`,
  with no surrogate id (the route is keyed by `skill_id`).

PII: goal `title`/`description`, review `period`/`strengths`/`improvements`,
feedback `comment` and employee-skill `note` are sensitive (`.ai/security.md`) —
stored as data only, never logged, emitted in events or written into audit
`changes` values. `rating`, `progress_pct` and `proficiency` are evaluative or
progress values and never appear in events either.

Statuses are lowercase stored strings with DB check constraints (the same
vocabulary the wire schemas pin). Indexes are tenant-first and declared in both
the models and migration 0018 (parity rule).
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
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


class Goal(Base):
    """One employee-owned goal: `draft → active → completed | cancelled`."""

    __tablename__ = "performance_goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "progress_pct >= 0 AND progress_pct <= 100",
            name="progress_pct_valid",
        ),
        # Role-scoped list with the `employee_id`/`status` filters, newest
        # first.
        Index(
            "ix_performance_goals_tenant_employee_status_created",
            "tenant_id",
            "employee_id",
            "status",
            "created_at",
        ),
        # The all-employees management list without an employee filter.
        Index(
            "ix_performance_goals_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    # PII — never logged, never in events, never in audit `changes` values.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Past due dates are allowed: a goal is a plan, not a claim.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
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


class Review(Base):
    """One employee review: `draft → completed | cancelled` (terminal)."""

    __tablename__ = "performance_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'completed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="rating_valid",
        ),
        # Subject-scoped list: managers see self + direct reports.
        Index(
            "ix_performance_reviews_tenant_employee_status_created",
            "tenant_id",
            "employee_id",
            "status",
            "created_at",
        ),
        # The author's own draft/complete worklist.
        Index(
            "ix_performance_reviews_tenant_author_status_created",
            "tenant_id",
            "author_id",
            "status",
            "created_at",
        ),
        # HR/Admin all-reviews list without a subject filter.
        Index(
            "ix_performance_reviews_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    # The subject (the employee being reviewed).
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    # The acting user id (ADR 0010), not an employee link — no FK.
    author_id: Mapped[UUID] = mapped_column(nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    period: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strengths: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    improvements: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
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


class ReviewProvider(Base):
    """One nominated feedback provider on one review (composite primary key)."""

    __tablename__ = "performance_review_providers"
    __table_args__ = (
        # The `feedback_for_me=true` worklist join and provider validation.
        Index(
            "ix_performance_review_providers_tenant_employee",
            "tenant_id",
            "employee_id",
            "review_id",
        ),
    )

    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("performance_reviews.id"), primary_key=True
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), primary_key=True
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewFeedback(Base):
    """One immutable peer comment per `(review_id, author_id)`."""

    __tablename__ = "performance_review_feedback"
    __table_args__ = (
        UniqueConstraint(
            "review_id",
            "author_id",
            name="uq_performance_review_feedback_review_author",
        ),
        # The review's feedback list, oldest first.
        Index(
            "ix_performance_review_feedback_tenant_review_created",
            "tenant_id",
            "review_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("performance_reviews.id"), nullable=False
    )
    # The nominated provider's employee id (matches the nomination).
    author_id: Mapped[UUID] = mapped_column(ForeignKey("employees.id"), nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    comment: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Skill(Base):
    """The per-tenant skill catalog with ADR 0007 soft delete."""

    __tablename__ = "performance_skills"
    __table_args__ = (
        # Uniqueness spans archived rows so restore never conflicts.
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_performance_skills_tenant_name",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(
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


class EmployeeSkill(Base):
    """One catalog skill attached to one employee, keyed by the skill id."""

    __tablename__ = "performance_employee_skills"
    __table_args__ = (
        CheckConstraint(
            "proficiency >= 1 AND proficiency <= 5",
            name="proficiency_valid",
        ),
        Index(
            "ix_performance_employee_skills_tenant_employee",
            "tenant_id",
            "employee_id",
        ),
    )

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), primary_key=True
    )
    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("performance_skills.id"), primary_key=True
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    proficiency: Mapped[int] = mapped_column(Integer, nullable=False)
    # PII — never logged, never in events, never in audit `changes` values.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
