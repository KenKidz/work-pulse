"""Postgres adapters for the outbox (ADR 0012).

`PostgresEventOutbox` is the write side: domains append finished envelopes
inside the request's session, so the business row, its audit entry and its
outbox row commit in one transaction (`.ai/architecture.md` → "Outbox, not
dual writes").

`PostgresOutboxRepository` is the relay side: the documented cross-tenant
system reader (alongside the audit writer) over unpublished rows plus the two
bookkeeping updates. It never inspects the payload beyond handing it to the
publisher.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.outbox import EventEnvelope, InvalidEnvelopeError


class PostgresEventOutbox:
    """Appends outbox rows inside the caller's transaction.

    The stored `payload` is the input dict **verbatim** — `event_id` and
    `occurred_at` are the builder's (the injected domain clock), never
    re-stamped. Validation is the port's write-time gate: a non-envelope
    payload raises `InvalidEnvelopeError`, failing the transaction loudly
    instead of storing a junk row.
    """

    def __init__(self, session: AsyncSession, clock: Callable[[], datetime]) -> None:
        self._session = session
        self._clock = clock

    async def append(self, envelope: dict[str, Any]) -> None:
        try:
            validated = EventEnvelope.model_validate(envelope)
        except ValidationError:
            # Generic message, `from None`: pydantic's detail embeds the input
            # payload, which must never reach logs or error surfaces
            # (`.ai/security.md`).
            raise InvalidEnvelopeError() from None

        self._session.add(
            EventOutboxRow(
                id=uuid7(),
                event_id=validated.event_id,
                tenant_id=validated.tenant_id,
                event_type=validated.event_type,
                payload=envelope,
                occurred_at=validated.occurred_at,
                created_at=self._clock(),
            )
        )
        await self._session.flush()


class PostgresOutboxRepository:
    """Relay-side access over `event_outbox` — no user identity, all tenants."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_unpublished(self, limit: int) -> Sequence[EventOutboxRow]:
        """Unpublished rows in insertion order (UUIDv7 `id`), oldest first."""
        stmt = (
            select(EventOutboxRow)
            .where(EventOutboxRow.published_at.is_(None))
            .order_by(EventOutboxRow.id)
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def mark_published(self, row_id: UUID, at: datetime) -> None:
        """Record a broker-acked publish — `attempts` counts every attempt."""
        await self._session.execute(
            update(EventOutboxRow)
            .where(EventOutboxRow.id == row_id)
            .values(published_at=at, attempts=EventOutboxRow.attempts + 1)
        )

    async def mark_failed(self, row_id: UUID, error: str) -> None:
        """Record a failed publish attempt. `error` is infrastructure error
        text only — never payload contents (`.ai/security.md`)."""
        await self._session.execute(
            update(EventOutboxRow)
            .where(EventOutboxRow.id == row_id)
            .values(last_error=error, attempts=EventOutboxRow.attempts + 1)
        )
