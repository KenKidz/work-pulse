"""SQLAlchemy model for the Documents domain (Phase 4+, T3).

One table, `document`: the metadata row for one employee file whose bytes live
in private object storage behind the `DocumentStorage` port (ADR 0022). The row
never carries the object key or a URL — the key is derived from
`(tenant_id, employee_id, document_id)` and presigned URLs are minted on demand.

- `deleted_at` is the soft-delete marker (ADR 0007's spirit): deleted rows are
  kept for traceability while the stored object is purged (PII minimization).
  Every visible read filters `deleted_at IS NULL`; a second delete is a `404`,
  deliberately unlike the `archived_at` idempotent no-op.
- `category` is the pinned lowercase enum (`contract|identification|
  certification|other`) with a DB check constraint; `size_bytes` is capped at
  the pinned 20 MB backstop. The service returns the stable `422` codes first;
  these constraints only guard direct writes.
- `uploaded_by` is the acting **user** id (ADR 0010), not an employee link, so
  there is no FK on it.
- The list index is partial on live rows (`deleted_at IS NULL`) and ordered
  `created_at DESC`, matching the employee Documents tab. It is declared here
  and in migration `0019` (model/migration parity rule).

PII: `title`, `filename` and the storage keys derived from the ids are
sensitive (`.ai/security.md`) — stored as data only, never logged, emitted in
events or written into audit `changes` values.
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
    desc,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class Document(Base):
    """One employee document's metadata (bytes live in private storage)."""

    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint(
            "category IN ('contract', 'identification', 'certification', 'other')",
            name="category_valid",
        ),
        CheckConstraint(
            "size_bytes >= 1 AND size_bytes <= 20971520",
            name="size_bytes_valid",
        ),
        # The employee Documents tab: live rows only, newest first, id for the
        # pagination tiebreak. Declared in migration 0019 in parity.
        Index(
            "ix_document_tenant_employee_created",
            "tenant_id",
            "employee_id",
            desc("created_at"),
            postgresql_where=text("deleted_at IS NULL"),
            postgresql_include=["id"],
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("employees.id"), nullable=False
    )
    # PII — never logged, never in events, never in audit `changes` values.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    # Past expiry dates are allowed: historical records are a legitimate state.
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # PII — the original client-supplied name; never logged or emitted.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # The acting user id (ADR 0010) — deliberately no FK.
    uploaded_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Soft delete only: visible reads filter this out and the object is purged.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
