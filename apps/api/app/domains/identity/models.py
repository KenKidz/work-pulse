"""SQLAlchemy models for the Identity domain.

`users` holds accounts: **globally unique** `email` (decision 7 — one account
belongs to exactly one tenant, and login resolves the tenant from the account),
one of the six fixed `role` values, an optional `employee_id` link (deliberately
no FK — the denormalized tenant column convention: the row's `tenant_id` is the
scope, the employee's existence is checked through `EmployeeLinkPort`), and the
argon2 password hash.

`sessions` holds opaque-token sessions: the cookie carries the 256-bit token,
`token_hash` stores its SHA-256 hexdigest (lookup key only — the hash is not a
secret), and `expires_at` is an absolute instant. Expired rows are rejected on
read; logout/revocation deletes rows.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Global email uniqueness (decision 7) — the DB backstop behind the
        # service's email pre-check, mapped to `user_email_taken` by constraint
        # name in the Postgres adapter.
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    employee_id: Mapped[UUID | None] = mapped_column(nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Session(Base):
    """One opaque-token session; deleted on logout, password change or reset."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
