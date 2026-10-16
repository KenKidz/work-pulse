"""Unit tests for the presence-delta publish hook on captures (T3).

Facts are identifiers and state only; every call passes an explicit `now` and
the publisher is a recording fake — no wall-clock or I/O anywhere.
"""

from datetime import UTC, datetime, time

from app.domains.attendance.schemas import CheckOutRequest
from app.domains.attendance.service import AttendanceService
from tests.unit.attendance.conftest import (
    ACTOR,
    TENANT_A,
    TODAY,
    check_in,
    identity,
    seed_employee,
    seed_shift,
)
from tests.unit.attendance.fakes import (
    FakeAttendanceRepo,
    FakePresencePublisher,
    FakeShiftRepo,
    RaisingPresencePublisher,
)
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

EARLY = datetime(2026, 9, 24, 2, 0, tzinfo=UTC)  # 09:00 in Asia/Ho_Chi_Minh
LATE_30 = datetime(2026, 9, 24, 2, 40, tzinfo=UTC)  # 30 minutes past start+grace
EVENING = datetime(2026, 9, 24, 10, 30, tzinfo=UTC)


async def test_on_time_check_in_publishes_a_present_fact(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    presence: FakePresencePublisher,
) -> None:
    seed_employee(attendance_repo)
    shift = await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    mutation = await check_in(service, now=EARLY)

    assert len(presence.facts) == 1
    fact = presence.facts[0]
    assert fact.tenant_id == TENANT_A
    assert fact.workday == TODAY
    assert fact.employee_id == ACTOR
    assert fact.attendance_id == mutation.record.id
    assert fact.status == "present"
    assert fact.late is False
    assert fact.minutes_late is None  # only set with `late` (wire rule)
    assert fact.since == EARLY
    assert fact.reason == "checked_in"
    assert fact.occurred_at == EARLY
    assert mutation.record.shift_id == shift.id


async def test_remote_check_in_publishes_a_remote_fact(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    presence: FakePresencePublisher,
) -> None:
    seed_employee(attendance_repo)  # no shift → never late

    await check_in(service, work_mode="remote")

    assert len(presence.facts) == 1
    assert presence.facts[0].status == "remote"
    assert presence.facts[0].late is False


async def test_late_check_in_carries_minutes_late_on_the_fact(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    presence: FakePresencePublisher,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)

    await check_in(service, now=LATE_30)

    assert len(presence.facts) == 1
    fact = presence.facts[0]
    assert fact.late is True
    assert fact.minutes_late == 30


async def test_check_out_publishes_checked_out_and_keeps_minutes_late(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    presence: FakePresencePublisher,
) -> None:
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)
    opened = await check_in(service, now=LATE_30)

    await service.check_out(identity(), opened.record.id, CheckOutRequest(), EVENING)

    assert [fact.reason for fact in presence.facts] == ["checked_in", "checked_out"]
    fact = presence.facts[-1]
    assert fact.status == "checked_out"
    assert fact.attendance_id == opened.record.id
    assert fact.late is True
    assert fact.minutes_late == 30  # recomputed, but the late check-in's value
    assert fact.since == EVENING
    assert fact.occurred_at == EVENING


async def test_minutes_late_is_recomputed_from_the_records_shift(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    presence: FakePresencePublisher,
) -> None:
    """A shift edit after a late check-in changes displayed minutes (risk 3).

    The `late` flag itself is stored and never drifts; only `minutes_late` is
    recomputed at read/publish time.
    """
    seed_employee(attendance_repo)
    shift = await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)
    opened = await check_in(service, now=LATE_30)
    assert presence.facts[0].minutes_late == 30

    await shift_repo.update(TENANT_A, shift.id, start_time=time(9, 15))
    await service.check_out(identity(), opened.record.id, CheckOutRequest(), EVENING)

    fact = presence.facts[-1]
    assert fact.late is True  # the flag never drifts
    assert fact.minutes_late == 15  # boundary moved 9:10 → 9:25 (local)


async def test_check_out_without_a_resolvable_shift_has_no_minutes(
    service: AttendanceService,
    attendance_repo: FakeAttendanceRepo,
    presence: FakePresencePublisher,
) -> None:
    seed_employee(attendance_repo)
    opened = await check_in(service, now=LATE_30)  # no shift → never late

    await service.check_out(identity(), opened.record.id, CheckOutRequest(), EVENING)

    fact = presence.facts[-1]
    assert fact.status == "checked_out"
    assert fact.late is False
    assert fact.minutes_late is None


async def test_a_raising_publisher_never_fails_a_capture(
    attendance_repo: FakeAttendanceRepo,
    shift_repo: FakeShiftRepo,
    audit: RecordingAuditWriter,
) -> None:
    """The port says publishers never raise; a violating adapter must not
    break check-in/check-out (best-effort fan-out)."""
    seed_employee(attendance_repo)
    await seed_shift(shift_repo, start_time=time(9, 0), grace_minutes=10)
    publisher = RaisingPresencePublisher()
    service = AttendanceService(
        repo=attendance_repo,
        shifts=shift_repo,
        audit=audit,
        presence=publisher,
        outbox=RecordingEventOutbox(),
    )

    opened = await check_in(service, now=LATE_30)
    assert opened.record.late is True

    closed = await service.check_out(
        identity(), opened.record.id, CheckOutRequest(), EVENING
    )
    assert closed.record.checked_out_at == EVENING

    assert publisher.calls == 2
    # Audit still ran for both captures — the failure was the bus only.
    assert [entry.action for entry in audit.entries] == [
        "attendance.checked_in",
        "attendance.checked_out",
    ]
