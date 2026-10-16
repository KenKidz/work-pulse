"""Unit tests for `RealtimeService` — the live board projection, no I/O.

`snapshot` maps the attendance board's one projection (`board_detail`); the
stream emits it as the first frame and then one `presence.upsert` per accepted
fact, with counts recounted over the connection's known rows. Attendance fakes
and a scripted stream keep every test I/O-free, and every judgement runs on
the explicit `now` passed in (`.ai/testing.md` rule 5).
"""

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.domains.attendance.service import AttendanceService, BoardDetail
from app.domains.realtime.service import RealtimeService
from app.shared.identity import Identity, Role
from app.shared.presence import (
    PresenceFact,
    PresenceReason,
    PresenceStatus,
    PresenceStream,
)
from tests.unit.attendance.conftest import (
    ACTOR,
    NOW,
    OTHER,
    REPORT,
    TENANT_A,
    TODAY,
    identity,
    seed_employee,
    seed_record,
)
from tests.unit.attendance.fakes import (
    FakeAttendanceRepo,
    FakePresencePublisher,
    FakeShiftRepo,
)
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
NEXT_DAY = TODAY + timedelta(days=1)


def _wire(value: datetime) -> str:
    """A UTC datetime as pydantic's JSON mode writes it (`Z`, not `+00:00`)."""
    return value.isoformat().replace("+00:00", "Z")


def _fact(
    *,
    employee_id: UUID = REPORT,
    workday: date = TODAY,
    status: PresenceStatus = "present",
    reason: PresenceReason = "checked_in",
    late: bool = False,
    minutes_late: int | None = None,
    since: datetime = NOW,
    occurred_at: datetime = NOW,
) -> PresenceFact:
    """One synthetic fact — identifiers and state only, deterministic clock."""
    return PresenceFact(
        tenant_id=TENANT_A,
        workday=workday,
        employee_id=employee_id,
        attendance_id=ATTENDANCE,
        status=status,
        late=late,
        minutes_late=minutes_late,
        since=since,
        reason=reason,
        occurred_at=occurred_at,
    )


class ScriptedStream:
    """A scripted presence bus: the facts, then end of stream.

    `script` replaces the fixed list when a test needs to change state between
    facts (e.g. a team change mid-stream). `tenants` records the subscription
    so tests can pin the channel the connection binds to.
    """

    def __init__(
        self, script: Callable[[], AsyncIterator[PresenceFact]] | None = None
    ) -> None:
        self.facts: list[PresenceFact] = []
        self._script = script
        self.tenants: list[UUID] = []

    async def subscribe(self, tenant_id: UUID) -> AsyncIterator[PresenceFact]:
        self.tenants.append(tenant_id)
        if self._script is not None:
            async for fact in self._script():
                yield fact
            return
        for fact in self.facts:
            yield fact


@dataclass
class Harness:
    repo: FakeAttendanceRepo
    shifts: FakeShiftRepo
    attendance: AttendanceService
    stream: ScriptedStream

    def service(self, stream: PresenceStream | None = None) -> RealtimeService:
        return RealtimeService(attendance=self.attendance, stream=stream or self.stream)


@pytest.fixture
def harness() -> Harness:
    repo = FakeAttendanceRepo()
    shifts = FakeShiftRepo()
    stream = ScriptedStream()
    attendance = AttendanceService(
        repo=repo,
        shifts=shifts,
        audit=RecordingAuditWriter(),
        presence=FakePresencePublisher(),
        outbox=RecordingEventOutbox(),
    )
    return Harness(repo=repo, shifts=shifts, attendance=attendance, stream=stream)


async def _frames(
    service: RealtimeService, caller: Identity | None = None
) -> list[dict[str, Any]]:
    """Drain the (finite) stream and parse its wire JSON frames."""
    raw = [frame async for frame in service.stream(caller or identity(), NOW)]
    return [json.loads(frame) for frame in raw]


async def test_stream_starts_with_the_snapshot_frame(harness: Harness) -> None:
    seed_employee(harness.repo)
    record = await seed_record(harness.repo)

    frames = await _frames(harness.service())

    assert len(frames) == 1  # nothing scripted: the snapshot is the only frame
    snapshot = frames[0]
    assert snapshot["type"] == "snapshot"
    assert snapshot["date"] == TODAY.isoformat()  # the injected clock's workday
    assert snapshot["counts"] == {
        "present": 1,
        "remote": 0,
        "late": 0,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }
    assert len(snapshot["presence"]) == 1
    row = snapshot["presence"][0]
    assert row["employee_id"] == str(ACTOR)
    assert row["status"] == "present"
    assert row["late"] is False
    assert row["attendance_id"] == str(record.id)
    assert datetime.fromisoformat(row["since"]) == NOW
    assert snapshot["activity"] == [
        {
            "occurred_at": _wire(NOW),
            "employee_id": str(ACTOR),
            "action": "checked_in",
        }
    ]
    assert harness.stream.tenants == [TENANT_A]  # its tenant channel only


async def test_in_scope_fact_upserts_the_row_and_recounts(harness: Harness) -> None:
    seed_employee(harness.repo, employee_id=ACTOR)
    await seed_record(harness.repo, employee_id=ACTOR)
    harness.stream.facts = [_fact(employee_id=REPORT, late=True, minutes_late=30)]

    frames = await _frames(harness.service())

    assert [frame["type"] for frame in frames] == ["snapshot", "presence.upsert"]
    delta = frames[1]
    assert delta["date"] == TODAY.isoformat()
    assert delta["reason"] == "checked_in"
    assert delta["occurred_at"] == _wire(NOW)
    assert delta["row"] == {
        "employee_id": str(REPORT),
        "status": "present",
        "late": True,
        "minutes_late": 30,
        "since": _wire(NOW),
        "attendance_id": str(ATTENDANCE),
    }
    assert delta["counts"] == {
        "present": 2,  # the snapshot's actor + the upserted report
        "remote": 0,
        "late": 1,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }


async def test_out_of_scope_facts_drop_and_a_team_change_reincludes(
    harness: Harness,
) -> None:
    """A transfer is picked up by the next bounded scope refresh (<= one window)."""
    seed_employee(harness.repo, employee_id=ACTOR)  # the manager
    seed_employee(harness.repo, employee_id=REPORT, manager_id=ACTOR)
    seed_employee(harness.repo, employee_id=OTHER)  # not a report yet

    async def script() -> AsyncIterator[PresenceFact]:
        yield _fact(employee_id=OTHER)  # out of scope: dropped, scope still fresh
        seed_employee(harness.repo, employee_id=OTHER, manager_id=ACTOR)
        yield _fact(employee_id=OTHER, occurred_at=NOW + timedelta(seconds=61))
        yield _fact(employee_id=REPORT, occurred_at=NOW + timedelta(seconds=61))

    frames = await _frames(
        harness.service(ScriptedStream(script=script)), identity(role=Role.MANAGER)
    )

    assert [frame["type"] for frame in frames] == [
        "snapshot",
        "presence.upsert",
        "presence.upsert",
    ]
    assert [frame["row"]["employee_id"] for frame in frames[1:]] == [
        str(OTHER),
        str(REPORT),
    ]
    assert frames[-1]["counts"]["present"] == 2


async def test_duplicate_facts_are_harmless(harness: Harness) -> None:
    seed_employee(harness.repo, employee_id=ACTOR)
    await seed_record(harness.repo, employee_id=ACTOR)
    fact = _fact(employee_id=REPORT)
    harness.stream.facts = [fact, fact]

    frames = await _frames(harness.service())

    assert [frame["type"] for frame in frames] == [
        "snapshot",
        "presence.upsert",
        "presence.upsert",
    ]
    assert frames[1]["row"] == frames[2]["row"]
    assert frames[1]["counts"] == frames[2]["counts"]  # upserted once, not twice
    assert frames[1]["counts"]["present"] == 2


async def test_a_delta_from_another_workday_is_forwarded_without_patching(
    harness: Harness,
) -> None:
    """The client refetches on a date mismatch; counts stay on the snapshot workday."""
    seed_employee(harness.repo, employee_id=ACTOR)
    await seed_record(harness.repo, employee_id=ACTOR)
    harness.stream.facts = [
        _fact(employee_id=REPORT, workday=NEXT_DAY),
        _fact(employee_id=REPORT),
    ]

    frames = await _frames(harness.service())

    assert [frame["type"] for frame in frames] == [
        "snapshot",
        "presence.upsert",
        "presence.upsert",
    ]
    foreign = frames[1]
    assert foreign["date"] == NEXT_DAY.isoformat()  # observable mismatch
    assert foreign["counts"] == frames[0]["counts"]  # never folded in
    current = frames[2]
    assert current["date"] == TODAY.isoformat()
    assert current["counts"]["present"] == 2  # the matching delta folds normally


class _CountingBoard:
    """Counts scope reads while delegating to the real attendance service."""

    def __init__(self, inner: AttendanceService) -> None:
        self._inner = inner
        self.scope_calls = 0

    async def board_detail(
        self, identity: Identity, board_date: date, now: datetime
    ) -> BoardDetail:
        return await self._inner.board_detail(identity, board_date, now)

    async def visible_employee_scope(
        self, identity: Identity
    ) -> frozenset[UUID] | None:
        self.scope_calls += 1
        return await self._inner.visible_employee_scope(identity)


async def test_out_of_scope_bursts_refresh_the_scope_once_per_window(
    harness: Harness,
) -> None:
    """A burst of out-of-scope facts never costs one scope query each."""
    seed_employee(harness.repo, employee_id=ACTOR)  # the manager, no reports
    seed_employee(harness.repo, employee_id=OTHER)
    board = _CountingBoard(harness.attendance)
    harness.stream.facts = [
        _fact(employee_id=OTHER),  # inside the connect window: dropped
        _fact(employee_id=OTHER, occurred_at=NOW + timedelta(seconds=10)),
        _fact(employee_id=OTHER, occurred_at=NOW + timedelta(seconds=70)),
    ]

    frames = await _frames(
        RealtimeService(attendance=board, stream=harness.stream),
        identity(role=Role.MANAGER),
    )

    assert [frame["type"] for frame in frames] == ["snapshot"]  # all dropped
    # The connect-time scope + exactly one bounded refresh, not three queries.
    assert board.scope_calls == 2


async def test_delta_counts_do_not_jump_after_a_re_check_in(harness: Harness) -> None:
    """The board counts a re-check-in once; deltas must not contradict it."""
    seed_employee(harness.repo, employee_id=ACTOR)
    seed_employee(harness.repo, employee_id=REPORT)
    await seed_record(
        harness.repo,
        employee_id=ACTOR,
        checked_in_at=datetime(2026, 9, 24, 2, 40, tzinfo=NOW.tzinfo),
        checked_out_at=datetime(2026, 9, 24, 6, 0, tzinfo=NOW.tzinfo),
        late=True,
        duration_minutes=200,
    )
    await seed_record(harness.repo, employee_id=ACTOR, checked_in_at=NOW)
    harness.stream.facts = [_fact(employee_id=REPORT)]

    frames = await _frames(harness.service())

    snapshot_counts = frames[0]["counts"]
    assert snapshot_counts == {
        "present": 1,
        "remote": 0,
        "late": 0,  # the superseded late record is not a current contribution
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }
    assert frames[1]["counts"] == {
        "present": 2,  # only the report's own delta changed
        "remote": 0,
        "late": 0,
        "on_leave": 0,
        "absent": 0,
        "checked_out": 0,
    }
