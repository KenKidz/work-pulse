"""Cross-domain presence port for the Realtime Command Center.

Attendance records a fact and hands it to a `PresencePublisher`; the realtime
domain consumes facts from a `PresenceStream`. Phase 2 wires both over Redis
pub/sub (`app/infrastructure/redis/`), but domains only see these protocols —
they never import Redis (`.ai/architecture.md` rule 3).

Identifiers and state only: a fact carries no names, emails, notes or any
other PII (`.ai/security.md`). The web joins display names client-side.
"""

from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

# A fact is produced by a check-in/check-out or by an approved leave covering
# the consume date (ADR 0014): `checked_out` replaces the row status at close;
# `present`/`remote` follow the work mode; `on_leave` wins over a same-day
# record and carries no attendance identity.
PresenceStatus = Literal["present", "remote", "checked_out", "on_leave"]
PresenceReason = Literal["checked_in", "checked_out", "leave_approved"]


class PresenceFact(BaseModel):
    """One presence transition, projected onto the live board.

    Attendance transitions carry a record id and `since`; the approved-leave
    fact carries neither (ADR 0014). Full-row semantics: consumers upsert
    `PresenceRow`-shaped state, so redelivery is harmless (at-most-once
    transport, idempotent payload).
    """

    tenant_id: UUID
    workday: date
    employee_id: UUID
    # `None` for the leave fact: an on-leave row has no attendance record and
    # no presence timestamp (ADR 0014).
    attendance_id: UUID | None = None
    status: PresenceStatus
    late: bool
    minutes_late: int | None
    since: datetime | None = None
    reason: PresenceReason
    occurred_at: datetime


class PresencePublisher(Protocol):
    """Best-effort publish port. Implementations must never raise."""

    async def publish(self, fact: PresenceFact) -> None: ...


class PresenceStream(Protocol):
    """Per-tenant subscription port. Yields facts published by any process."""

    def subscribe(self, tenant_id: UUID) -> AsyncIterator[PresenceFact]: ...
