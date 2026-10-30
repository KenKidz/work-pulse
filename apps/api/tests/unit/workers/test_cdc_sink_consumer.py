"""T4 unit tests — the CDC sink loop against in-memory stubs (no I/O).

The real `CdcSinkConsumer` runs with a scripted source and a fake projection
writer (`.ai/testing.md`): changes are written then committed in order; a
fresh group fences `ready = 0` before its first fetch and flips on snapshot
evidence; a restart with committed offsets never re-fences a served
projection; delete/truncate ops are dropped and counted; malformed rows are
committed skips; a store failure retries head-of-line; and an unknown leave
type retries a bounded number of times before the counted `unknown` fallback.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from aiokafka.errors import UnknownTopicOrPartitionError

from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)
from workers.cdc_sink.consumer import CdcSinkConsumer
from workers.cdc_sink.main import _start_source_with_retry
from workers.cdc_sink.mapping import LeaveTypeBook

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_B = UUID("00000000-0000-4000-8000-000000000002")
DEPARTMENT = UUID("00000000-0000-4000-8000-0000000000d1")
POSITION = UUID("00000000-0000-4000-8000-0000000000e1")
LOCATION = UUID("00000000-0000-4000-8000-0000000000f1")
OVERTIME = UUID("00000000-0000-4000-8000-0000000000c1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c3")
LEAVE = UUID("00000000-0000-4000-8000-0000000000c4")
LEAVE_TYPE = UUID("00000000-0000-4000-8000-0000000000c5")

EPOCH = date(1970, 1, 1)
WORKDAY = date(2026, 10, 23)
EFFECTIVE_FROM = date(2026, 6, 1)
HIRED_AT = date(2026, 1, 5)
COMMIT_TS = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
COMMIT_MS = int(COMMIT_TS.timestamp() * 1000)
FENCE_TS = COMMIT_TS - timedelta(minutes=5)
LATER_TS = COMMIT_TS + timedelta(minutes=5)


# --- wire fixtures ------------------------------------------------------------


def _epoch_days(value: date) -> int:
    return (value - EPOCH).days


def _envelope(
    table: str,
    after: dict[str, Any] | None,
    *,
    op: str = "c",
    snapshot: object = "false",
    before: dict[str, Any] | None = None,
    ts_ms: int = COMMIT_MS,
) -> dict[str, Any]:
    return {
        "before": before,
        "after": after,
        "source": {
            "ts_ms": ts_ms,
            "lsn": 1,
            "snapshot": snapshot,
            "db": "workpulse",
            "schema": "public",
            "table": table,
        },
        "op": op,
        "ts_ms": ts_ms + 1,
        "transaction": None,
    }


def _wire(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _employee_after(employee_id: UUID = EMPLOYEE) -> dict[str, Any]:
    return {
        "id": str(employee_id),
        "tenant_id": str(TENANT),
        "hired_at": _epoch_days(HIRED_AT),
        "terminated_at": None,
    }


def _assignment_after() -> dict[str, Any]:
    return {
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "department_id": str(DEPARTMENT),
        "position_id": str(POSITION),
        "location_id": str(LOCATION),
        "effective_from": _epoch_days(EFFECTIVE_FROM),
        "effective_to": None,
    }


def _attendance_after() -> dict[str, Any]:
    return {
        "id": str(ATTENDANCE),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "workday": _epoch_days(WORKDAY),
        "duration_minutes": 468,
        "late": True,
        "minutes_late": 12,
    }


def _overtime_after(status: str = "hr_verified") -> dict[str, Any]:
    return {
        "id": str(OVERTIME),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "work_date": _epoch_days(WORKDAY),
        "hours": "2.50",
        "status": status,
    }


def _leave_after(status: str = "approved") -> dict[str, Any]:
    return {
        "id": str(LEAVE),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "leave_type_id": str(LEAVE_TYPE),
        "start_date": _epoch_days(WORKDAY),
        "days": 2,
        "status": status,
    }


def _leave_type_after() -> dict[str, Any]:
    return {"id": str(LEAVE_TYPE), "tenant_id": str(TENANT), "code": "annual"}


# --- stubs --------------------------------------------------------------------


class StubMessage:
    def __init__(self, value: bytes | None) -> None:
        self.value = value


class StubSource:
    """Scripted source: `getmany` hands out one queued batch per call."""

    def __init__(
        self,
        batches: Sequence[Sequence[bytes]] = (),
        *,
        commit_failures: int = 0,
        journal: list[Any] | None = None,
    ) -> None:
        self._batches = [list(batch) for batch in batches]
        self._commit_failures = commit_failures
        self.journal = journal if journal is not None else []
        self.on_drained: Callable[[], None] | None = None
        self.fetches = 0
        self.commits = 0

    async def getmany(self, *, timeout_ms: int) -> Mapping[Any, Sequence[StubMessage]]:
        self.fetches += 1
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
    """In-memory CDC writer: records readiness writes, rows and failures."""

    def __init__(
        self,
        *,
        ready_state: tuple[int, datetime] | None = None,
        journal: list[Any] | None = None,
    ) -> None:
        self.ready_state = ready_state
        self.journal = journal if journal is not None else []
        self.fences = 0
        self.ready_writes = 0
        self.fences_remaining = 0
        self.ready_remaining = 0
        self.flush_failures = 0
        self.on_upsert: Callable[[], None] | None = None
        self.attempts: dict[str, int] = {}
        self.employees: dict[UUID, tuple[EmployeeRow, datetime]] = {}
        self.assignments: dict[tuple[UUID, date], tuple[AssignmentRow, datetime]] = {}
        self.attendance: dict[UUID, tuple[AttendanceFact, datetime]] = {}
        self.overtime: dict[UUID, tuple[OvertimeFact, datetime]] = {}
        self.leave: dict[UUID, tuple[LeaveFact, datetime]] = {}

    def readiness(self) -> tuple[int, datetime] | None:
        return self.ready_state

    def mark_rebuilding(self) -> None:
        if self.fences_remaining > 0:
            self.fences_remaining -= 1
            raise RuntimeError("clickhouse down")
        self.fences += 1
        self.ready_state = (0, FENCE_TS)
        self.journal.append("fence")

    def mark_ready(self) -> None:
        if self.ready_remaining > 0:
            self.ready_remaining -= 1
            raise RuntimeError("clickhouse down")
        self.ready_writes += 1
        self.ready_state = (1, LATER_TS)
        self.journal.append("ready")

    def flush(self) -> None:
        self.journal.append("flush")
        if self.flush_failures > 0:
            self.flush_failures -= 1
            raise RuntimeError("clickhouse down")

    def upsert_employee(self, row: EmployeeRow, updated_at: datetime) -> None:
        self._write("employee", row.employee_id, row, updated_at)
        self.employees[row.employee_id] = (row, updated_at)

    def upsert_assignment(self, row: AssignmentRow, updated_at: datetime) -> None:
        self._write("assignment", row.employee_id, row, updated_at)
        self.assignments[(row.employee_id, row.effective_from)] = (row, updated_at)

    def upsert_attendance(self, fact: AttendanceFact, updated_at: datetime) -> None:
        self._write("attendance", fact.attendance_id, fact, updated_at)
        self.attendance[fact.attendance_id] = (fact, updated_at)

    def upsert_overtime(self, fact: OvertimeFact, updated_at: datetime) -> None:
        self._write("overtime", fact.overtime_id, fact, updated_at)
        self.overtime[fact.overtime_id] = (fact, updated_at)

    def upsert_leave(self, fact: LeaveFact, updated_at: datetime) -> None:
        self._write("leave", fact.leave_request_id, fact, updated_at)
        self.leave[fact.leave_request_id] = (fact, updated_at)

    def _write(self, kind: str, key: UUID, row: object, updated_at: datetime) -> None:
        self.attempts[kind] = self.attempts.get(kind, 0) + 1
        self.journal.append((kind, key, updated_at))
        if self.on_upsert is not None:
            self.on_upsert()


def _consumer(
    source: StubSource,
    writer: FakeWriter,
    *,
    fresh_group: bool = False,
    reference_retries: int = 3,
    leave_types: LeaveTypeBook | None = None,
) -> CdcSinkConsumer:
    return CdcSinkConsumer(
        source,
        writer,
        poll_seconds=0.001,
        backoff_base=0.001,
        backoff_max=0.01,
        fresh_group=fresh_group,
        leave_types=leave_types,
        reference_retries=reference_retries,
    )


def _record_pauses(
    monkeypatch: pytest.MonkeyPatch,
    consumer: CdcSinkConsumer,
    pauses: list[float],
) -> None:
    async def fake_pause(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(consumer, "_pause", fake_pause)


async def _run(consumer: CdcSinkConsumer, source: StubSource) -> None:
    source.on_drained = consumer.stop
    await consumer.run()


# --- the loop ------------------------------------------------------------------


async def test_changes_are_written_then_committed_in_order() -> None:
    journal: list[Any] = []
    source = StubSource(
        [
            [
                _wire(_envelope("employees", _employee_after())),
                _wire(_envelope("employments", _assignment_after())),
                _wire(_envelope("attendance_records", _attendance_after())),
                _wire(_envelope("overtime_requests", _overtime_after())),
                _wire(_envelope("leave_types", _leave_type_after())),
                _wire(_envelope("leave_requests", _leave_after())),
            ]
        ],
        journal=journal,
    )
    writer = FakeWriter(journal=journal)
    consumer = _consumer(source, writer)

    await _run(consumer, source)

    assert journal.count("commit") == 1
    assert journal[-2:] == ["flush", "commit"]
    assert journal[0] == ("employee", EMPLOYEE, COMMIT_TS)
    assert journal[1] == ("assignment", EMPLOYEE, COMMIT_TS)
    assert writer.employees[EMPLOYEE][1] == COMMIT_TS
    assert writer.assignments[(EMPLOYEE, EFFECTIVE_FROM)][1] == COMMIT_TS
    assert writer.attendance[ATTENDANCE][0].minutes_late == 12
    assert writer.overtime[OVERTIME][0].status == "hr_verified"
    assert writer.leave[LEAVE][0].leave_type_code == "annual"
    assert source.commits == 1
    assert consumer.started.is_set()


async def test_fresh_group_fences_before_the_first_fetch() -> None:
    journal: list[Any] = []
    source = StubSource(
        [[_wire(_envelope("employees", _employee_after(), op="r", snapshot="last"))]],
        journal=journal,
    )
    writer = FakeWriter(ready_state=None, journal=journal)
    consumer = _consumer(source, writer, fresh_group=True)

    await _run(consumer, source)

    assert writer.fences == 1
    assert writer.ready_writes == 1
    assert journal[0] == "fence"
    assert "ready" in journal
    assert writer.ready_state is not None
    assert writer.ready_state[0] == 1


async def test_fresh_group_retries_a_failed_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal: list[Any] = []
    source = StubSource(
        [[_wire(_envelope("employees", _employee_after(), op="r", snapshot="last"))]],
        journal=journal,
    )
    writer = FakeWriter(ready_state=None, journal=journal)
    writer.fences_remaining = 1
    consumer = _consumer(source, writer, fresh_group=True)
    pauses: list[float] = []
    _record_pauses(monkeypatch, consumer, pauses)

    await _run(consumer, source)

    assert pauses
    assert writer.fences == 1
    assert writer.ready_state is not None
    assert writer.ready_state[0] == 1


async def test_restart_with_a_served_projection_does_not_touch_readiness() -> None:
    journal: list[Any] = []
    source = StubSource(
        [[_wire(_envelope("employees", _employee_after(), op="r", snapshot="last"))]],
        journal=journal,
    )
    writer = FakeWriter(ready_state=(1, FENCE_TS), journal=journal)
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.fences == 0
    assert writer.ready_writes == 0
    assert journal == [
        ("employee", EMPLOYEE, COMMIT_TS),
        "flush",
        "commit",
    ]


async def test_restart_with_an_open_fence_flips_it_on_evidence() -> None:
    journal: list[Any] = []
    source = StubSource(
        [
            [
                _wire(
                    _envelope("employees", _employee_after(), op="r", snapshot="true")
                ),
                _wire(
                    _envelope("employees", _employee_after(), op="r", snapshot="last")
                ),
            ]
        ],
        journal=journal,
    )
    writer = FakeWriter(ready_state=(0, FENCE_TS), journal=journal)
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.fences == 0
    assert writer.ready_writes == 1
    assert journal.count("ready") == 1


async def test_snapshot_rows_in_progress_do_not_flip_readiness() -> None:
    source = StubSource(
        [
            [
                _wire(
                    _envelope("employees", _employee_after(), op="r", snapshot="true")
                ),
                _wire(
                    _envelope(
                        "employments", _assignment_after(), op="r", snapshot="true"
                    )
                ),
            ]
        ]
    )
    writer = FakeWriter(ready_state=(0, FENCE_TS))
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.ready_writes == 0
    assert writer.ready_state == (0, FENCE_TS)


async def test_first_non_snapshot_change_flips_a_fenced_projection() -> None:
    source = StubSource([[_wire(_envelope("employees", _employee_after()))]])
    writer = FakeWriter(ready_state=(0, FENCE_TS))
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.ready_writes == 1


async def test_heartbeat_flips_an_empty_snapshot() -> None:
    source = StubSource([[_wire({"ts_ms": COMMIT_MS})]])
    writer = FakeWriter(ready_state=(0, FENCE_TS))
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.ready_writes == 1
    assert source.commits == 1


async def test_stale_evidence_does_not_flip_but_later_evidence_does() -> None:
    source = StubSource(
        [
            [
                _wire(
                    _envelope(
                        "employees",
                        _employee_after(),
                        op="r",
                        snapshot="last",
                        ts_ms=COMMIT_MS,
                    )
                )
            ],
            [
                _wire(
                    _envelope(
                        "employees",
                        _employee_after(EMPLOYEE_B),
                        op="r",
                        snapshot="last",
                        ts_ms=COMMIT_MS + 600_000,
                    )
                )
            ],
        ]
    )
    # The fence is newer than the first batch's evidence (history replayed from
    # before the rebuild) but older than the second batch's.
    writer = FakeWriter(ready_state=(0, LATER_TS))
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.ready_writes == 1
    assert writer.ready_state is not None
    assert writer.ready_state[0] == 1


async def test_missing_marker_does_not_touch_readiness() -> None:
    source = StubSource(
        [[_wire(_envelope("employees", _employee_after(), op="r", snapshot="last"))]]
    )
    writer = FakeWriter(ready_state=None)
    consumer = _consumer(source, writer, fresh_group=False)

    await _run(consumer, source)

    assert writer.fences == 0
    assert writer.ready_writes == 0


async def test_delete_and_truncate_are_dropped_and_counted(
    telemetry_harness: Any,
) -> None:
    journal: list[Any] = []
    source = StubSource(
        [
            [
                _wire(_envelope("employees", None, op="d", before=_employee_after())),
                _wire(_envelope("employees", None, op="t")),
            ]
        ],
        journal=journal,
    )
    writer = FakeWriter(ready_state=(1, FENCE_TS), journal=journal)
    consumer = _consumer(source, writer)

    await _run(consumer, source)

    assert writer.employees == {}
    assert source.commits == 1
    assert "workpulse_cdc_dropped_deletes_total" in telemetry_harness.metrics()


async def test_redelivered_changes_converge() -> None:
    envelope = _wire(_envelope("overtime_requests", _overtime_after()))
    source = StubSource([[envelope], [envelope]])
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer)

    await _run(consumer, source)

    assert len(writer.overtime) == 1
    assert writer.attempts["overtime"] == 2
    assert writer.overtime[OVERTIME][1] == COMMIT_TS
    assert source.commits == 2


async def test_leave_pending_is_committed_without_a_row() -> None:
    source = StubSource([[_wire(_envelope("leave_requests", _leave_after("pending")))]])
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer)

    await _run(consumer, source)

    assert writer.leave == {}
    assert source.commits == 1


async def test_same_batch_reference_is_applied_before_the_leave_fact() -> None:
    source = StubSource(
        [
            [
                _wire(_envelope("leave_requests", _leave_after())),
                _wire(_envelope("leave_types", _leave_type_after())),
            ]
        ]
    )
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer, reference_retries=0)

    await _run(consumer, source)

    assert writer.leave[LEAVE][0].leave_type_code == "annual"


async def test_unknown_leave_type_retries_then_falls_back_and_counts(
    telemetry_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([[_wire(_envelope("leave_requests", _leave_after()))]])
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer, reference_retries=2)
    pauses: list[float] = []
    _record_pauses(monkeypatch, consumer, pauses)

    await _run(consumer, source)

    assert pauses
    assert writer.leave[LEAVE][0].leave_type_code == "unknown"
    assert source.commits == 1
    assert "workpulse_cdc_reference_misses_total" in telemetry_harness.metrics()


async def test_fallback_mapping_error_is_a_committed_skip() -> None:
    bad = {**_leave_after(), "employee_id": "not-a-uuid"}
    source = StubSource([[_wire(_envelope("leave_requests", bad))]])
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer, reference_retries=0)

    await _run(consumer, source)

    assert writer.leave == {}
    assert source.commits == 1


async def test_store_failure_retries_the_batch_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([[_wire(_envelope("employees", _employee_after()))]])
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    writer.flush_failures = 1
    consumer = _consumer(source, writer)
    pauses: list[float] = []
    _record_pauses(monkeypatch, consumer, pauses)

    await _run(consumer, source)

    assert writer.attempts["employee"] == 1
    assert pauses
    assert source.commits == 1
    assert writer.employees[EMPLOYEE][1] == COMMIT_TS


async def test_malformed_and_invalid_rows_are_skipped_and_committed() -> None:
    source = StubSource(
        [
            [
                b"not json",
                _wire({"hello": "world"}),
                _wire(
                    _envelope("employees", {**_employee_after(), "tenant_id": "bad"})
                ),
            ]
        ]
    )
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer)

    await _run(consumer, source)

    assert writer.employees == {}
    assert source.commits == 1


async def test_stop_mid_batch_does_not_commit_the_page() -> None:
    source = StubSource(
        [
            [
                _wire(_envelope("employees", _employee_after())),
                _wire(_envelope("employees", _employee_after(EMPLOYEE_B))),
            ]
        ]
    )
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer)
    writer.on_upsert = consumer.stop

    await consumer.run()

    assert source.commits == 0
    assert list(writer.employees) == [EMPLOYEE]
    assert "flush" not in writer.journal
    assert "commit" not in writer.journal


async def test_commit_failure_never_kills_the_loop() -> None:
    source = StubSource(
        [[_wire(_envelope("employees", _employee_after()))]], commit_failures=1
    )
    writer = FakeWriter(ready_state=(1, FENCE_TS))
    consumer = _consumer(source, writer)

    await _run(consumer, source)

    assert writer.employees
    assert source.commits == 0
    assert source.fetches >= 2


class FlakyStartSource:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.starts = 0

    async def start(self) -> None:
        self.starts += 1
        if self.failures > 0:
            self.failures -= 1
            raise UnknownTopicOrPartitionError()


async def test_startup_retries_until_the_connector_creates_topics() -> None:
    source = FlakyStartSource(failures=2)

    await _start_source_with_retry(source, backoff_base=0.001, backoff_max=0.002)

    assert source.starts == 3


class BrokenStartSource:
    async def start(self) -> None:
        raise RuntimeError("broker down")


async def test_startup_does_not_swallow_a_broker_failure() -> None:
    with pytest.raises(RuntimeError, match="broker down"):
        await _start_source_with_retry(
            BrokenStartSource(), backoff_base=0.001, backoff_max=0.002
        )
