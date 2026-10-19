"""Wire contracts for the Realtime Command Center.

The REST snapshot and the WebSocket frames share one payload vocabulary; every
frame validates against `contracts/realtime/board.v1.json` (a projection
protocol, not the event catalog). Identifiers and state only — never names,
emails, notes or any other PII (`.ai/security.md`); the web joins display
names client-side.

Semantics worth naming here (pinned in `.ai/features/realtime-command-center.md`):

- `date` is the current workday (the server's UTC date); a delta whose `date`
  differs from a client's snapshot makes the client refetch instead of patch.
- Counts **overlap, they are not a partition** (ADR 0009): `late` co-occurs
  with `present`/`remote`/`checked_out`; `on_leave` reflects APPROVED-leave
  coverage (ADR 0014).
- Every push is a full-row idempotent upsert with fresh full counts — clients
  never derive counts and never patch partial rows.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.shared.presence import PresenceReason

# Live presence vocabulary — one status plus the orthogonal `late` flag
# (ADR 0009). `late` is never a status; `on_leave` is produced for employees
# covered by APPROVED leave (ADR 0014).
PresenceStatus = Literal["present", "remote", "checked_out", "absent", "on_leave"]


class PresenceRow(BaseModel):
    """One employee's presence for the workday — the full-row upsert shape.

    `minutes_late` is recomputed from the record's shift at read/publish time
    (the stored `late` flag itself is immutable); `since` is `checked_in_at`
    while open, `checked_out_at` once closed, `null` for absent/on_leave rows.
    """

    employee_id: UUID
    status: PresenceStatus
    late: bool
    minutes_late: int | None = None
    since: datetime | None = None
    attendance_id: UUID | None = None


class RealtimeBoardCounts(BaseModel):
    """Independent counts — not a partition (a person can be present and late)."""

    present: int
    remote: int
    late: int
    on_leave: int  # APPROVED-leave coverage for the date (ADR 0014)
    absent: int
    checked_out: int


class RealtimeActivityEntry(BaseModel):
    """One recent attendance transition for the activity rail.

    Names are joined web-side from the employee data the web already holds.
    """

    occurred_at: datetime
    employee_id: UUID
    action: PresenceReason


class RealtimeBoardSnapshot(BaseModel):
    """The one board payload: REST `200` of `GET /realtime/board`.

    The `snapshot` frame is this body plus `type` (see
    `RealtimeSnapshotFrame`), so the first WS frame and the REST body can
    never drift. Rows are unordered (the client sorts); `presence` is
    uncapped, `activity` is newest-first capped 20.
    """

    date: date
    counts: RealtimeBoardCounts
    presence: list[PresenceRow]
    activity: list[RealtimeActivityEntry]


class RealtimeSnapshotFrame(RealtimeBoardSnapshot):
    """First frame after the handshake, and again after every reconnect resync."""

    type: Literal["snapshot"] = "snapshot"


class RealtimePresenceUpsertFrame(BaseModel):
    """One attendance transition: the changed row plus fresh full counts."""

    type: Literal["presence.upsert"] = "presence.upsert"
    date: date
    counts: RealtimeBoardCounts
    row: PresenceRow
    occurred_at: datetime
    reason: PresenceReason
