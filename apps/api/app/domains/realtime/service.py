"""Use cases for the Realtime Command Center — the live presence wall.

The snapshot is the attendance board's one projection (`board_detail`, T3)
mapped to the realtime wire; the stream emits that snapshot as the first frame
and then one `presence.upsert` per attendance transition, with full counts
recounted over the connection's known rows (`PresenceState`). PostgreSQL is
the source of truth; Redis pub/sub only carries deltas (transient,
at-most-once — a missed delta is recovered by the client's ~60s snapshot
refresh or a reconnect resync).

Pinned rules implemented here (`.ai/features/realtime-command-center.md`):

- Read-only and tenant-bound: one subscription to
  `realtime:tenant:{tenant_id}`; the adapter binds every payload to the
  channel it arrived on, so cross-tenant facts never arrive.
- Scope mirrors the board exactly through
  `AttendanceService.visible_employee_scope` (manager: self + direct reports;
  unlinked manager: nobody; hr/admin/super_admin: tenant-wide). A fact outside
  the scope is dropped after a bounded lazy scope refresh
  (`SCOPE_REFRESH_INTERVAL`), so team changes apply within one refresh and a
  burst never costs one query per fact — out-of-scope rows are never leaked.
- A delta whose workday differs from the connection's snapshot workday is
  forwarded with its own `date` — the client reacts by refetching the REST
  snapshot instead of patching (pinned client rule) — and is not folded into
  the connection's counts: it belongs to another workday.
"""

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domains.attendance.service import BoardDetail
from app.domains.realtime.models import PresenceState
from app.domains.realtime.schemas import (
    PresenceRow,
    RealtimeActivityEntry,
    RealtimeBoardCounts,
    RealtimeBoardSnapshot,
    RealtimePresenceUpsertFrame,
    RealtimeSnapshotFrame,
)
from app.shared.identity import Identity
from app.shared.presence import PresenceFact, PresenceStream

SCOPE_REFRESH_INTERVAL = timedelta(seconds=60)
"""At most one lazy scope refresh per connection per event-time window.

Mirrors the client's ~60s snapshot resync: a transferred employee joins the
wall within one refresh, while a burst of out-of-scope facts never costs one
scope query per fact per connection. Measured on the facts' injected
`occurred_at`, never a wall clock.
"""


class AttendanceBoard(Protocol):
    """The board projection reads the wall needs.

    Implementations own their storage lifetime: the REST provider passes a
    request-scoped `AttendanceService`, while the WS provider reads through
    short-lived sessions so a viewer never pins a pooled PostgreSQL connection
    while it waits on Redis.
    """

    async def board_detail(
        self, identity: Identity, board_date: date, now: datetime
    ) -> BoardDetail: ...

    async def visible_employee_scope(
        self, identity: Identity
    ) -> frozenset[UUID] | None: ...


def _row(fact: PresenceFact) -> PresenceRow:
    """The full-row upsert a fact projects to (identifiers and state only)."""
    return PresenceRow(
        employee_id=fact.employee_id,
        status=fact.status,
        late=fact.late,
        minutes_late=fact.minutes_late,
        since=fact.since,
        attendance_id=fact.attendance_id,
    )


def _in_scope(scope: frozenset[UUID] | None, employee_id: UUID) -> bool:
    """`None` means tenant-wide (hr/admin/super_admin)."""
    return scope is None or employee_id in scope


class RealtimeService:
    """Snapshot + live-stream use cases; one instance per request."""

    def __init__(self, attendance: AttendanceBoard, stream: PresenceStream) -> None:
        self._attendance = attendance
        self._stream = stream

    async def snapshot(
        self, identity: Identity, now: datetime
    ) -> RealtimeBoardSnapshot:
        """The whole board as of `now` — the REST body and the first WS frame.

        A mapping of `AttendanceService.board_detail` (the one board
        projection), so the wall and `GET /attendance/board` can never
        disagree about workday, lateness or `absent` (ADR 0009).
        """
        detail = await self._attendance.board_detail(identity, now.date(), now)
        return RealtimeBoardSnapshot(
            date=detail.date,
            counts=RealtimeBoardCounts.model_validate(
                detail.counts, from_attributes=True
            ),
            presence=[
                PresenceRow.model_validate(row, from_attributes=True)
                for row in detail.rows
            ],
            activity=[
                RealtimeActivityEntry(
                    occurred_at=entry.occurred_at,
                    employee_id=entry.employee_id,
                    action=entry.action.value,
                )
                for entry in detail.activity
            ],
        )

    async def stream(self, identity: Identity, now: datetime) -> AsyncIterator[str]:
        """Wire-ready frames for one connection: snapshot, then deltas.

        The connection's `PresenceState` is built here and never shared, so a
        reconnect always resyncs from a fresh snapshot. Frames are JSON text
        (the router sends them verbatim); each one is a full payload — rows
        and counts are never patched client-side.
        """
        snapshot = await self.snapshot(identity, now)
        state = PresenceState.from_snapshot(snapshot)
        yield RealtimeSnapshotFrame(
            date=snapshot.date,
            counts=snapshot.counts,
            presence=snapshot.presence,
            activity=snapshot.activity,
        ).model_dump_json()

        scope = await self._attendance.visible_employee_scope(identity)
        last_scope_refresh = now
        async for fact in self._stream.subscribe(identity.tenant_id):
            if not _in_scope(scope, fact.employee_id):
                # A team change applies within one refresh; the interval keeps
                # a burst of out-of-scope facts from costing one scope query
                # each per connection (never a leak — out-of-scope rows are
                # always dropped server-side).
                if fact.occurred_at - last_scope_refresh >= SCOPE_REFRESH_INTERVAL:
                    scope = await self._attendance.visible_employee_scope(identity)
                    last_scope_refresh = fact.occurred_at
                if not _in_scope(scope, fact.employee_id):
                    continue

            row = _row(fact)
            if fact.workday != state.workday:
                # Another workday (location timezone / UTC rollover): forward
                # it so the client's date check triggers a snapshot refetch,
                # but keep the connection's counts on its snapshot workday.
                yield RealtimePresenceUpsertFrame(
                    date=fact.workday,
                    counts=state.counts,
                    row=row,
                    occurred_at=fact.occurred_at,
                    reason=fact.reason,
                ).model_dump_json()
                continue

            state.upsert(row)
            yield RealtimePresenceUpsertFrame(
                date=state.workday,
                counts=state.counts,
                row=row,
                occurred_at=fact.occurred_at,
                reason=fact.reason,
            ).model_dump_json()
