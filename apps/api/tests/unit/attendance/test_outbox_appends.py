"""T4 unit — captures append their envelopes to the transactional outbox.

The service hands every produced envelope to the `EventOutbox` port in builder
order, inside the same call (the Postgres adapter flushes it in the request's
transaction, ADR 0012), while `AttendanceMutation.events` stays the
shape-identical tuple the router always saw. No I/O: fakes only.
"""

from datetime import UTC, datetime, time

import pytest

from app.domains.attendance.schemas import CheckOutRequest
from app.domains.attendance.service import AttendanceService
from app.domains.employees.exceptions import EmployeeNotFoundError
from tests.unit.attendance.conftest import (
    check_in,
    identity,
    seed_employee,
    seed_record,
    seed_shift,
)
from tests.unit.attendance.fakes import (
    FakeAttendanceRepo,
    FakePresencePublisher,
    FakeShiftRepo,
)
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

EARLY = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)  # 08:00 in Asia/Ho_Chi_Minh
LATE_30 = datetime(2026, 9, 24, 2, 40, tzinfo=UTC)  # 30 minutes past start+grace
EVENING = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


async def test_on_time_check_in_appends_only_checked_in(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(attendance_repo)

    mutation = await check_in(service, now=EARLY)

    assert [env["event_type"] for env in outbox.envelopes] == ["attendance.checked_in"]
    assert outbox.envelopes == list(mutation.events)  # same envelopes, same order


async def test_late_check_in_appends_checked_in_then_late(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    mutation = await check_in(service, now=LATE_30)

    assert [env["event_type"] for env in outbox.envelopes] == [
        "attendance.checked_in",
        "attendance.late",
    ]
    assert outbox.envelopes == list(mutation.events)
    assert outbox.envelopes[1]["data"]["minutes_late"] == 30


async def test_check_out_appends_checked_out(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(attendance_repo)
    record = await seed_record(attendance_repo, checked_in_at=EARLY)

    mutation = await service.check_out(
        identity(), record.id, CheckOutRequest(), EVENING
    )

    assert [env["event_type"] for env in outbox.envelopes] == ["attendance.checked_out"]
    assert outbox.envelopes == list(mutation.events)
    assert (
        outbox.envelopes[0]["data"]["duration_minutes"]
        == mutation.record.duration_minutes
    )


async def test_rejected_check_in_appends_nothing(
    service: AttendanceService, outbox: RecordingEventOutbox
) -> None:
    """A mutation that fails before its append leaves the outbox untouched."""
    with pytest.raises(EmployeeNotFoundError):
        await check_in(service)

    assert outbox.envelopes == []


async def test_outbox_failure_propagates_before_presence_and_audit(
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    audit: RecordingAuditWriter,
    presence: FakePresencePublisher,
) -> None:
    """An append failure is never swallowed: the capture fails loudly.

    The service must not catch outbox errors (the real adapter's failure has
    to roll the request transaction back), and the append must precede the
    presence publish and the audit write — neither may run for a capture whose
    event was never stored.
    """
    seed_employee(attendance_repo)
    failing = RecordingEventOutbox(error=RuntimeError("outbox unavailable"))
    service = AttendanceService(
        repo=attendance_repo,
        shifts=shift_repo,
        audit=audit,
        presence=presence,
        outbox=failing,
    )

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await check_in(service)

    assert [env["event_type"] for env in failing.envelopes] == ["attendance.checked_in"]
    assert presence.facts == []
    assert audit.entries == []
