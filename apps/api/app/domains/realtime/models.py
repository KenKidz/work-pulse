"""The connection-scoped live presence projection for the command-center wall.

`PresenceState` is the hot state one WebSocket connection maintains: the rows
it currently knows — the snapshot's rows ∪ every accepted delta — plus their
recounted counts. It is deliberately not an ORM model and never shared between
connections or processes: PostgreSQL stays the source of truth, Redis only
moves deltas (`.ai/features/realtime-command-center.md`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.domains.realtime.schemas import (
    PresenceRow,
    RealtimeBoardCounts,
    RealtimeBoardSnapshot,
)


def _recount(rows: Mapping[UUID, PresenceRow]) -> RealtimeBoardCounts:
    """Overlapping ADR 0009 counts over a known row set (never a partition).

    One row per employee (newest wins), so a re-check-in replaces its row
    instead of counting twice. `late` is a cut across the statuses.
    """
    values = list(rows.values())
    return RealtimeBoardCounts(
        present=sum(row.status == "present" for row in values),
        remote=sum(row.status == "remote" for row in values),
        late=sum(row.late for row in values),
        on_leave=sum(row.status == "on_leave" for row in values),
        absent=sum(row.status == "absent" for row in values),
        checked_out=sum(row.status == "checked_out" for row in values),
    )


@dataclass
class PresenceState:
    """One connection's known board state: workday, rows map, full counts."""

    workday: date
    rows: dict[UUID, PresenceRow]
    counts: RealtimeBoardCounts

    @classmethod
    def from_snapshot(cls, snapshot: RealtimeBoardSnapshot) -> "PresenceState":
        """Seed the connection state from its first frame's payload.

        The snapshot's own counts are kept (they are the board projection's,
        ADR 0009); every accepted delta then recounts over the known rows.
        """
        rows = {row.employee_id: row for row in snapshot.presence}
        return cls(workday=snapshot.date, rows=rows, counts=snapshot.counts)

    def upsert(self, row: PresenceRow) -> PresenceRow:
        """Full-row idempotent upsert + recount; returns the applied row.

        `on_leave` is canonical for the workday (ADR 0014): an attendance
        transition arriving after an approval — a permitted check-out of an
        already-open record — must not replace the leave row, or the delta
        would disagree with the REST snapshot. The applied (preserved) row is
        returned so the caller forwards a frame that still matches the state.
        """
        existing = self.rows.get(row.employee_id)
        if (
            existing is not None
            and existing.status == "on_leave"
            and row.status != "on_leave"
        ):
            return existing
        self.rows[row.employee_id] = row
        self.counts = _recount(self.rows)
        return row
