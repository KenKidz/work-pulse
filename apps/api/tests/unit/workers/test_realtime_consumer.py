"""T3 unit tests — the realtime consumer loop against stubs (no I/O).

The real `RealtimeConsumer` runs with an in-memory source and a recording
publisher (`.ai/testing.md`): each message is published then committed, in
order and exactly once per message; filtered (`attendance.late`,
`employee.*`) and malformed messages are skipped without killing the loop,
and the offset still advances past them; a raising publisher is swallowed
ids-only and the offset advances too (the transient contract, proven for the
write path's best-effort publish before the ADR 0013 swap); `stop()` ends the
loop between messages with the in-flight one finished; fetch failures back
off exponentially and never kill the loop; and `started` fires only after the
first completed fetch round — the readiness signal the gate's log line waits
on (never a sleep).
"""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
import structlog

from app.domains.attendance.events import attendance_checked_in, attendance_late
from app.domains.leave.events import leave_approved
from app.shared.presence import PresenceFact, PresencePublisher
from workers.realtime.consumer import EventSource, RealtimeConsumer

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE_A = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_B = UUID("00000000-0000-4000-8000-000000000002")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000e1")
NOW = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 16)
# A leak test value: it must never appear in a log line (ids-only contract).
SENTINEL = "Zorblax Qqweird pii-unique@example.test"


def _envelope_bytes(employee_id: UUID = EMPLOYEE_A, *, late: bool = False) -> bytes:
    """A real builder envelope on the wire (the producer's exact shape)."""
    envelope = attendance_checked_in(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=employee_id,
        workday=WORKDAY,
        work_mode="onsite",
        late=late,
        minutes_late=30 if late else None,
    )
    return json.dumps(envelope).encode("utf-8")


class StubMessage:
    """The fetched-record shape the loop reads (`message.value`)."""

    def __init__(self, value: bytes | None) -> None:
        self.value = value


class StubSource:
    """Scripted source: `getmany` hands out one queued batch per call.

    `on_drained` fires when the queue is empty — tests wire `consumer.stop`
    there so the loop terminates; `failures` makes the first fetches raise
    (a broker outage); `commit_failures` makes that many commits raise.
    """

    def __init__(
        self,
        batches: Sequence[Sequence[bytes]] = (),
        *,
        failures: int = 0,
        commit_failures: int = 0,
    ) -> None:
        self._batches = [list(batch) for batch in batches]
        self._failures = failures
        self._commit_failures = commit_failures
        self.journal: list[Any] = []
        self.on_drained: Callable[[], None] | None = None
        self.fetches = 0
        self.commits = 0  # successful commits only

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


class RecordingPublisher:
    """Records published facts; optional error and per-attempt hook."""

    def __init__(
        self,
        error: BaseException | None = None,
        on_publish: Callable[[PresenceFact], None] | None = None,
        journal: list[Any] | None = None,
    ) -> None:
        self.facts: list[PresenceFact] = []
        self.error = error
        self.on_publish = on_publish
        self.journal = journal if journal is not None else []

    async def publish(self, fact: PresenceFact) -> None:
        self.facts.append(fact)
        self.journal.append(("publish", str(fact.employee_id)))
        if self.on_publish is not None:
            self.on_publish(fact)
        if self.error is not None:
            raise self.error


def _consumer(
    source: EventSource,
    publisher: PresencePublisher,
    *,
    poll_seconds: float = 0.001,
    backoff_base: float = 0.5,
    backoff_max: float = 8.0,
    today: Callable[[], date] | None = None,
) -> RealtimeConsumer:
    options: dict[str, Any] = {
        "poll_seconds": poll_seconds,
        "backoff_base": backoff_base,
        "backoff_max": backoff_max,
    }
    if today is not None:
        options["today"] = today
    return RealtimeConsumer(source, publisher, **options)


def _record_pauses(
    monkeypatch: pytest.MonkeyPatch, consumer: RealtimeConsumer, *, stop_after: int
) -> list[float]:
    """Replace the loop's sleep with a recorder; the Nth pause stops the loop."""
    pauses: list[float] = []

    async def fake_pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= stop_after:
            consumer.stop()

    monkeypatch.setattr(consumer, "_pause", fake_pause)
    return pauses


async def test_each_message_is_published_then_committed_in_order() -> None:
    source = StubSource([[_envelope_bytes(EMPLOYEE_A), _envelope_bytes(EMPLOYEE_B)]])
    publisher = RecordingPublisher(journal=source.journal)
    consumer = _consumer(source, publisher)
    source.on_drained = consumer.stop

    await consumer.run()

    assert [fact.employee_id for fact in publisher.facts] == [EMPLOYEE_A, EMPLOYEE_B]
    # The offset advances only after the publish attempt, one message at a time.
    assert source.journal == [
        ("publish", str(EMPLOYEE_A)),
        "commit",
        ("publish", str(EMPLOYEE_B)),
        "commit",
    ]
    assert source.commits == 2
    assert consumer.started.is_set()


async def test_stop_finishes_the_in_flight_message_then_exits() -> None:
    source = StubSource(
        [[_envelope_bytes(EMPLOYEE_A), _envelope_bytes(EMPLOYEE_B), _envelope_bytes()]]
    )
    publisher = RecordingPublisher()
    consumer = _consumer(source, publisher)
    publisher.on_publish = lambda _fact: (
        consumer.stop() if len(publisher.facts) == 2 else None
    )

    await consumer.run()

    # The in-flight message finished (published + committed); the third never
    # started and stays unconsumed.
    assert [fact.employee_id for fact in publisher.facts] == [EMPLOYEE_A, EMPLOYEE_B]
    assert source.commits == 2
    assert source.fetches == 1


async def test_a_raising_publisher_is_swallowed_and_the_offset_advances() -> None:
    source = StubSource([[_envelope_bytes(EMPLOYEE_A), _envelope_bytes(EMPLOYEE_B)]])
    publisher = RecordingPublisher(error=RuntimeError("redis gone"))
    consumer = _consumer(source, publisher)
    source.on_drained = consumer.stop

    await consumer.run()  # must not raise — the transient contract

    assert len(publisher.facts) == 2  # both attempts happened
    assert source.commits == 2  # and both offsets advanced anyway
    assert consumer.started.is_set()


async def test_a_commit_failure_never_kills_the_loop() -> None:
    source = StubSource(
        [[_envelope_bytes(EMPLOYEE_A), _envelope_bytes(EMPLOYEE_B)]],
        commit_failures=1,
    )
    publisher = RecordingPublisher(journal=source.journal)
    consumer = _consumer(source, publisher)
    source.on_drained = consumer.stop

    await consumer.run()

    assert [fact.employee_id for fact in publisher.facts] == [EMPLOYEE_A, EMPLOYEE_B]
    assert source.journal == [
        ("publish", str(EMPLOYEE_A)),
        "commit",  # failed — the offset stayed put and the message may redeliver
        ("publish", str(EMPLOYEE_B)),
        "commit",
    ]
    assert source.commits == 1


async def test_malformed_and_foreign_messages_are_skipped_and_the_loop_continues() -> (
    None
):
    late = attendance_late(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE_A,
        workday=WORKDAY,
        shift_id=ATTENDANCE,
        minutes_late=30,
    )
    hired = {"event_type": "employee.hired", "version": 1, "data": {}}
    source = StubSource(
        [
            [
                b"this is not json",
                b'"a scalar, not an envelope"',
                json.dumps(late).encode("utf-8"),
                json.dumps(hired).encode("utf-8"),
                _envelope_bytes(EMPLOYEE_B),
            ]
        ]
    )
    publisher = RecordingPublisher()
    consumer = _consumer(source, publisher)
    source.on_drained = consumer.stop

    await consumer.run()

    assert [fact.employee_id for fact in publisher.facts] == [EMPLOYEE_B]
    assert source.commits == 5  # every message was handled — skips advance too


async def test_filtered_events_produce_no_facts_and_no_skip_logs() -> None:
    structlog.configure(cache_logger_on_first_use=False)
    late = attendance_late(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        attendance_id=ATTENDANCE,
        employee_id=EMPLOYEE_A,
        workday=WORKDAY,
        shift_id=ATTENDANCE,
        minutes_late=30,
    )
    hired = {"event_type": "employee.hired", "version": 1, "data": {}}
    source = StubSource(
        [[json.dumps(late).encode("utf-8"), json.dumps(hired).encode("utf-8")]]
    )
    publisher = RecordingPublisher()
    consumer = _consumer(source, publisher)
    source.on_drained = consumer.stop

    with structlog.testing.capture_logs() as logs:
        await consumer.run()

    assert publisher.facts == []
    skipped = [entry for entry in logs if entry["event"] == "consumer_message_skipped"]
    assert skipped == []  # filtered is silent — not a skip
    assert source.commits == 2


async def test_skip_logs_never_echo_payload_contents() -> None:
    structlog.configure(cache_logger_on_first_use=False)
    envelope = json.loads(_envelope_bytes(EMPLOYEE_A))
    del envelope["data"]["employee_id"]  # ours, but not fact-shaped
    envelope["data"]["note"] = SENTINEL  # hostile extra content, not a contract field
    source = StubSource([[json.dumps(envelope).encode("utf-8")]])
    publisher = RecordingPublisher()
    consumer = _consumer(source, publisher)
    source.on_drained = consumer.stop

    with structlog.testing.capture_logs() as logs:
        await consumer.run()

    assert publisher.facts == []  # never a partial fact
    skipped = [entry for entry in logs if entry["event"] == "consumer_message_skipped"]
    assert len(skipped) == 1
    dumped = json.dumps(logs, default=str)
    assert SENTINEL not in dumped
    assert str(envelope["event_id"]) in dumped  # the event id names the skip


async def test_fetch_failures_back_off_and_never_kill_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([[_envelope_bytes()]], failures=2)
    publisher = RecordingPublisher()
    consumer = _consumer(source, publisher, backoff_base=0.5)
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    await consumer.run()

    assert pauses == [0.5, 1.0]  # two failures: base, then double
    assert [fact.employee_id for fact in publisher.facts] == [EMPLOYEE_A]


async def test_started_fires_only_after_the_first_completed_fetch_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = StubSource([], failures=2)
    consumer = _consumer(source, RecordingPublisher())
    source.on_drained = consumer.stop
    pauses = _record_pauses(monkeypatch, consumer, stop_after=99)

    assert not consumer.started.is_set()

    await consumer.run()

    assert pauses == [0.5, 1.0]  # failed rounds never signalled readiness
    assert consumer.started.is_set()


async def test_stop_before_the_first_fetch_touches_nothing() -> None:
    source = StubSource([[_envelope_bytes()]])
    consumer = _consumer(source, RecordingPublisher())

    consumer.stop()
    await consumer.run()

    assert source.fetches == 0
    assert source.commits == 0
    assert not consumer.started.is_set()


def test_backoff_caps_at_the_maximum() -> None:
    consumer = _consumer(
        StubSource(), RecordingPublisher(), backoff_base=1.0, backoff_max=5.0
    )

    assert [consumer._backoff_seconds(n) for n in range(1, 5)] == [1.0, 2.0, 4.0, 5.0]
    assert consumer._backoff_seconds(5000) == 5.0  # bounded exponent, no overflow


def test_non_positive_poll_seconds_is_rejected() -> None:
    with pytest.raises(ValueError):
        _consumer(StubSource(), RecordingPublisher(), poll_seconds=0)


def test_invalid_backoff_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        _consumer(StubSource(), RecordingPublisher(), backoff_base=2.0, backoff_max=1.0)


# --- leave window -------------------------------------------------------------


def _leave_bytes(*, start: date, end: date) -> bytes:
    envelope = leave_approved(
        tenant_id=TENANT,
        actor_id=ACTOR,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST,
        employee_id=EMPLOYEE_A,
        leave_type_code="annual",
        start_date=start,
        end_date=end,
        days=(end - start).days + 1,
        decided_by=ACTOR,
    )
    return json.dumps(envelope).encode("utf-8")


async def test_leave_window_uses_the_injected_consume_date() -> None:
    covering = _leave_bytes(start=WORKDAY, end=WORKDAY)
    ended = _leave_bytes(start=date(2026, 10, 14), end=date(2026, 10, 15))
    future = _leave_bytes(start=date(2026, 10, 17), end=date(2026, 10, 19))
    source = StubSource([[covering, ended, future]])
    publisher = RecordingPublisher()
    consumer = _consumer(source, publisher, today=lambda: WORKDAY)
    source.on_drained = consumer.stop

    await consumer.run()

    assert len(publisher.facts) == 1
    fact = publisher.facts[0]
    assert fact.employee_id == EMPLOYEE_A
    assert fact.status == "on_leave"
    assert fact.reason == "leave_approved"
    assert fact.workday == WORKDAY
    assert fact.attendance_id is None and fact.since is None
    assert source.commits == 3  # filtered messages still advance the offset
