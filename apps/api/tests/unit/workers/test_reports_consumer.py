"""T6 unit tests — the reports consumer loop against in-memory stubs (no I/O).

The real `ReportsConsumer` runs with a scripted source and a fake projection
writer (`.ai/testing.md`): each message is folded/written then committed, in
order and exactly once per message; a transfer's fold carries its bounded
re-stamp; malformed, unknown and malformed-payload messages are skipped and
the offset still advances; known-but-irrelevant events are filtered silently;
a ClickHouse failure retries the same message with backoff and never commits
past it; commit/fetch failures never kill the loop; and `started` fires only
after the first completed fetch round — the readiness signal the gate's log
line waits on (never a sleep).
"""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import structlog

from app.domains.attendance.events import (
    attendance_checked_in,
    attendance_checked_out,
)
from app.domains.employees.events import employee_created, employee_transferred
from app.domains.leave.events import leave_approved
from app.domains.overtime.events import overtime_requested, overtime_verified
from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    DimensionFold,
    DimensionState,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)
from workers.reports.consumer import EventSource, ReportsConsumer

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE_A = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_B = UUID("00000000-0000-4000-8000-000000000002")
DEPARTMENT_A = UUID("00000000-0000-4000-8000-0000000000d1")
DEPARTMENT_B = UUID("00000000-0000-4000-8000-0000000000d2")
POSITION_A = UUID("00000000-0000-4000-8000-0000000000e1")
POSITION_B = UUID("00000000-0000-4000-8000-0000000000e2")
LOCATION_A = UUID("00000000-0000-4000-8000-0000000000f1")
LOCATION_B = UUID("00000000-0000-4000-8000-0000000000f2")
OVERTIME_A = UUID("00000000-0000-4000-8000-0000000000c1")
OVERTIME_B = UUID("00000000-0000-4000-8000-0000000000c2")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c3")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000c4")
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
HIRED_AT = date(2026, 1, 5)
WORKDAY = date(2026, 10, 23)
TRANSFER_DATE = date(2026, 6, 1)
# A leak test value: it must never appear in a log line (ids-only contract).
SENTINEL = "Zorblax Qqweird pii-unique@example.test"


# --- envelopes ----------------------------------------------------------------


def _created(employee_id: UUID = EMPLOYEE_A) -> dict[str, Any]:
    return employee_created(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=employee_id,
        department_id=DEPARTMENT_A,
        position_id=POSITION_A,
        location_id=LOCATION_A,
        employment_type="full_time",
        hired_at=HIRED_AT,
    )


def _transferred(employee_id: UUID = EMPLOYEE_A) -> dict[str, Any]:
    return employee_transferred(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        employee_id=employee_id,
        from_department_id=DEPARTMENT_A,
        to_department_id=DEPARTMENT_B,
        effective_from=TRANSFER_DATE,
        to_position_id=POSITION_B,
        to_location_id=LOCATION_B,
    )


def _requested(
    overtime_id: UUID = OVERTIME_A,
    *,
    employee_id: UUID = EMPLOYEE_A,
    work_date: date = WORKDAY,
    hours: Decimal = Decimal("2.50"),
) -> dict[str, Any]:
    return overtime_requested(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        overtime_id=overtime_id,
        employee_id=employee_id,
        work_date=work_date,
        hours=hours,
    )


def _verified(
    overtime_id: UUID = OVERTIME_A,
    *,
    employee_id: UUID = EMPLOYEE_A,
    work_date: date = WORKDAY,
    hours: Decimal = Decimal("2.50"),
) -> dict[str, Any]:
    return overtime_verified(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        overtime_id=overtime_id,
        employee_id=employee_id,
        work_date=work_date,
        hours=hours,
        verified_by=ACTOR,
    )


def _checked_in() -> dict[str, Any]:
    return attendance_checked_in(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE_A,
        workday=WORKDAY,
        work_mode="onsite",
        late=True,
        minutes_late=12,
    )


def _checked_out() -> dict[str, Any]:
    return attendance_checked_out(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE_A,
        workday=WORKDAY,
        duration_minutes=468,
        late=True,
        minutes_late=12,
    )


def _approved() -> dict[str, Any]:
    return leave_approved(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE_A,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=WORKDAY,
        days=2,
        decided_by=ACTOR,
    )


def _wire(envelope: Mapping[str, Any]) -> bytes:
    return json.dumps(envelope).encode("utf-8")


# --- stubs --------------------------------------------------------------------


class StubMessage:
    """The fetched-record shape the loop reads (`message.value`)."""

    def __init__(self, value: bytes | None) -> None:
        self.value = value


class StubSource:
    """Scripted source: `getmany` hands out one queued batch per call."""

    def __init__(
        self,
        batches: Sequence[Sequence[bytes]] = (),
        *,
        failures: int = 0,
        commit_failures: int = 0,
        journal: list[Any] | None = None,
    ) -> None:
        self._batches = [list(batch) for batch in batches]
        self._failures = failures
        self._commit_failures = commit_failures
        self.journal = journal if journal is not None else []
        self.on_drained: Callable[[], None] | None = None
        self.fetches = 0
        self.commits = 0

    async def getmany(self, *, timeout_ms: int) -> Mapping[Any, Sequence[StubMessage]]:
        self.fetches += 1
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("broker unreachable")
        if not self._batches:
            if self.on_drained is not None:
                self.on_drained()
            return {}
        return {0: [StubMessage(value) for value in self._batches.pop(0)]}

    async def commit(self) -> None:
        self.journal.append("commit")
        if self._commit_failures > 0:
            self._commit_failures -= 1
            raise RuntimeError("coordinator left the group")
        self.commits += 1


class FakeWriter:
    """In-memory `ProjectionWriter`: applies folds, records writes + failures."""

    def __init__(self, journal: list[Any] | None = None) -> None:
        self.journal = journal if journal is not None else []
        self.employees: dict[UUID, EmployeeRow] = {}
        self.assignments: dict[UUID, list[AssignmentRow]] = {}
        self.overtime: dict[UUID, OvertimeFact] = {}
        self.attendance: dict[UUID, AttendanceFact] = {}
        self.leave: dict[UUID, LeaveFact] = {}
        self.failures: dict[str, int] = {}

    def load_dimensions(self, tenant_id: UUID, employee_id: UUID) -> DimensionState:
        assignments = sorted(
            self.assignments.get(employee_id, []),
            key=lambda assignment: assignment.effective_from,
        )
        return DimensionState(
            employee=self.employees.get(employee_id),
            assignments=tuple(assignments),
        )

    def get_overtime(self, tenant_id: UUID, overtime_id: UUID) -> OvertimeFact | None:
        return self.overtime.get(overtime_id)

    def get_attendance(
        self, tenant_id: UUID, attendance_id: UUID
    ) -> AttendanceFact | None:
        return self.attendance.get(attendance_id)

    def get_leave(self, tenant_id: UUID, leave_request_id: UUID) -> LeaveFact | None:
        return self.leave.get(leave_request_id)

    def apply_dimension_fold(self, fold: DimensionFold) -> None:
        if fold.employee is not None:
            self.employees[fold.employee.employee_id] = fold.employee
        for assignment in fold.assignments:
            intervals = {
                row.effective_from: row
                for row in self.assignments.get(assignment.employee_id, [])
            }
            intervals[assignment.effective_from] = assignment
            self.assignments[assignment.employee_id] = list(intervals.values())
        self.journal.append(("dimension", fold.restamp_from))

    def upsert_overtime(self, fact: OvertimeFact) -> None:
        self._maybe_fail("overtime")
        self.overtime[fact.overtime_id] = fact
        self.journal.append(("overtime", fact.status))

    def upsert_attendance(self, fact: AttendanceFact) -> None:
        self._maybe_fail("attendance")
        self.attendance[fact.attendance_id] = fact
        self.journal.append(("attendance", fact.duration_minutes))

    def upsert_leave(self, fact: LeaveFact) -> None:
        self._maybe_fail("leave")
        self.leave[fact.leave_request_id] = fact
        self.journal.append(("leave", fact.approved_day_count))

    def _maybe_fail(self, kind: str) -> None:
        remaining = self.failures.get(kind, 0)
        if remaining > 0:
            self.failures[kind] = remaining - 1
            raise RuntimeError("clickhouse down")


def _consumer(
    source: EventSource,
    writer: FakeWriter,
    *,
    poll_seconds: float = 0.001,
    backoff_base: float = 0.5,
    backoff_max: float = 8.0,
) -> ReportsConsumer:
    return ReportsConsumer(
        source,
        writer,
        poll_seconds=poll_seconds,
        backoff_base=backoff_base,
        backoff_max=backoff_max,
    )


def _record_pauses(
    monkeypatch: pytest.MonkeyPatch, consumer: ReportsConsumer, *, stop_after: int
) -> list[float]:
    pauses: list[float] = []

    async def fake_pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= stop_after:
            consumer.stop()

    monkeypatch.setattr(consumer, "_pause", fake_pause)
    return pauses


# --- the loop ------------------------------------------------------------------


async def test_each_message_is_folded_written_then_committed_in_order() -> None:
    journal: list[Any] = []
    source = StubSource(
        [
            [
                _wire(_created()),
                _wire(_requested()),
                _wire(_verified()),
                _wire(_checked_in()),
                _wire(_checked_out()),
                _wire(_approved()),
            ]
        ],
        journal=journal,
    )
    writer = FakeWriter(journal=journal)
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop

    await consumer.run()

    assert source.journal == [
        ("dimension", None),
        "commit",
        ("overtime", "requested"),
        "commit",
        ("overtime", "hr_verified"),
        "commit",
        ("attendance", None),
        "commit",
        ("attendance", 468),
        "commit",
        ("leave", 2),
        "commit",
    ]
    assert writer.overtime[OVERTIME_A].status == "hr_verified"
    assert writer.attendance[ATTENDANCE].duration_minutes == 468
    assert writer.attendance[ATTENDANCE].late is True
    assert writer.leave[LEAVE_REQUEST].approved_day_count == 2
    assert source.commits == 6
    assert consumer.started.is_set()


async def test_a_transfer_fold_carries_the_bounded_restamp() -> None:
    journal: list[Any] = []
    source = StubSource([[_wire(_created()), _wire(_transferred())]], journal=journal)
    writer = FakeWriter(journal=journal)
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop

    await consumer.run()

    assert source.journal == [
        ("dimension", None),
        "commit",
        ("dimension", TRANSFER_DATE),
        "commit",
    ]
    assignments = sorted(
        writer.assignments[EMPLOYEE_A], key=lambda row: row.effective_from
    )
    assert [row.effective_from for row in assignments] == [HIRED_AT, TRANSFER_DATE]
    assert assignments[0].effective_to == TRANSFER_DATE
    assert assignments[1].department_id == DEPARTMENT_B
    assert assignments[1].position_id == POSITION_B
    assert assignments[1].location_id == LOCATION_B


async def test_malformed_unknown_and_malformed_payloads_skip_and_the_loop_lives() -> (
    None
):
    structlog.configure(cache_logger_on_first_use=False)
    malformed = _requested()
    del malformed["data"]["hours"]
    malformed["data"]["reason"] = SENTINEL
    unknown = {
        "event_id": "evt_reports_unknown",
        "event_type": f"mystery.{SENTINEL}",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }
    source = StubSource(
        [
            [
                b"this is not json",
                b'"a scalar, not an envelope"',
                _wire(unknown),
                _wire(malformed),
                _wire(_requested(OVERTIME_B)),
            ]
        ]
    )
    writer = FakeWriter()
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop

    with structlog.testing.capture_logs() as logs:
        await consumer.run()

    assert set(writer.overtime) == {OVERTIME_B}
    assert source.commits == 5  # every message was handled — skips advance too
    errors = {entry.get("error") for entry in logs}
    assert "malformed_envelope" in errors
    assert "unknown_event_type" in errors
    assert "invalid_envelope" in errors
    dumped = json.dumps(logs, default=str)
    assert "evt_reports_unknown" in dumped  # ids-only identification
    assert SENTINEL not in dumped  # hostile event type/payload never echoed
    assert "hours is missing" in dumped  # a field name, never its value


async def test_known_but_irrelevant_events_produce_no_skip_logs() -> None:
    structlog.configure(cache_logger_on_first_use=False)
    late = {
        "event_id": "evt_attendance_late",
        "event_type": "attendance.late",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }
    payroll = {
        "event_id": "evt_payroll_calculated",
        "event_type": "payroll.calculated",
        "version": 1,
        "tenant_id": str(TENANT),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR),
        "data": {},
    }
    source = StubSource([[_wire(late), _wire(payroll)]])
    writer = FakeWriter()
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop

    with structlog.testing.capture_logs() as logs:
        await consumer.run()

    assert writer.journal == []
    assert source.commits == 2
    assert [
        entry for entry in logs if entry["event"] == "consumer_message_skipped"
    ] == []


async def test_a_store_failure_retries_the_same_message_and_commits_after_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal: list[Any] = []
    source = StubSource([[_wire(_created()), _wire(_requested())]], journal=journal)
    writer = FakeWriter(journal=journal)
    writer.failures["overtime"] = 1
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    await consumer.run()

    assert pauses == [0.5]  # one store failure, then the retry succeeded
    assert source.commits == 2  # the dimension write and the retried fact
    assert source.journal == [
        ("dimension", None),
        "commit",
        ("overtime", "requested"),
        "commit",
    ]
    assert OVERTIME_A in writer.overtime


async def test_a_commit_failure_never_kills_the_loop() -> None:
    journal: list[Any] = []
    source = StubSource(
        [[_wire(_created()), _wire(_requested()), _wire(_requested(OVERTIME_B))]],
        commit_failures=1,
        journal=journal,
    )
    writer = FakeWriter(journal=journal)
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop

    await consumer.run()

    assert source.journal == [
        ("dimension", None),
        "commit",  # failed — the offset stayed put and may redeliver
        ("overtime", "requested"),
        "commit",
        ("overtime", "requested"),
        "commit",
    ]
    assert source.commits == 2
    assert set(writer.overtime) == {OVERTIME_A, OVERTIME_B}


async def test_fetch_failures_back_off_and_never_kill_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([[_wire(_created()), _wire(_requested())]], failures=2)
    writer = FakeWriter()
    consumer = _consumer(source, writer)
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    await consumer.run()

    assert pauses == [0.5, 1.0]  # two failures: base, then double
    assert OVERTIME_A in writer.overtime


async def test_started_fires_only_after_the_first_completed_fetch_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([], failures=2)
    consumer = _consumer(source, FakeWriter())
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    assert not consumer.started.is_set()

    await consumer.run()

    assert pauses == [0.5, 1.0]  # failed rounds never signalled readiness
    assert consumer.started.is_set()


async def test_stop_before_the_first_fetch_touches_nothing() -> None:
    source = StubSource([[_wire(_created())]])
    consumer = _consumer(source, FakeWriter())

    consumer.stop()
    await consumer.run()

    assert source.fetches == 0
    assert source.commits == 0
    assert not consumer.started.is_set()


def test_backoff_caps_at_the_maximum() -> None:
    consumer = _consumer(StubSource(), FakeWriter(), backoff_base=1.0, backoff_max=5.0)

    assert [consumer._backoff_seconds(n) for n in range(1, 5)] == [1.0, 2.0, 4.0, 5.0]
    assert consumer._backoff_seconds(5000) == 5.0  # bounded exponent, no overflow


def test_non_positive_poll_seconds_is_rejected() -> None:
    with pytest.raises(ValueError):
        _consumer(StubSource(), FakeWriter(), poll_seconds=0)


def test_invalid_backoff_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        _consumer(StubSource(), FakeWriter(), backoff_base=2.0, backoff_max=1.0)
