"""Outbox ORM model — the relay's entire world (ADR 0012).

Append-only like `audit_entries`: producers only insert; the relay only marks
`published_at` and its bookkeeping (`attempts`, `last_error`). Published rows
are kept as the record of what went on the bus — pruning is a separate ops
decision.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.base import Base


class EventOutboxRow(Base):
    __tablename__ = "event_outbox"
    __table_args__ = (
        # Relay poll: unpublished rows only, in UUIDv7 (insertion) order.
        Index(
            "ix_event_outbox_unpublished",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index("ix_event_outbox_tenant_id", "tenant_id"),
        Index("ix_event_outbox_event_type", "event_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
