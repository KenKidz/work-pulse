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

# A fact is only ever produced by a check-in or a check-out: `checked_out`
# replaces the row status at close; `present`/`remote` follow the work mode.
PresenceStatus = Literal["present", "remote", "checked_out"]
PresenceReason = Literal["checked_in", "checked_out"]


class PresenceFact(BaseModel):
    """One attendance transition, projected onto the live board.

    Full-row semantics: consumers upsert `PresenceRow`-shaped state, so
    redelivery is harmless (at-most-once transport, idempotent payload).
    """

    tenant_id: UUID
    workday: date
    employee_id: UUID
    attendance_id: UUID
    status: PresenceStatus
    late: bool
    minutes_late: int | None
    since: datetime
    reason: PresenceReason
    occurred_at: datetime


class PresencePublisher(Protocol):
    """Best-effort publish port. Implementations must never raise."""

    async def publish(self, fact: PresenceFact) -> None: ...


class PresenceStream(Protocol):
    """Per-tenant subscription port. Yields facts published by any process."""

    def subscribe(self, tenant_id: UUID) -> AsyncIterator[PresenceFact]: ...
