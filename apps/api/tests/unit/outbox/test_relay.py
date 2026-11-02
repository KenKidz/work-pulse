"""T7 unit tests — the relay loop and publisher against stubs (no I/O).

The real `OutboxRelay` runs with in-memory ports, so the behavior that matters
is proven without a database or a broker (`.ai/testing.md`): happy batches
publish then mark in `id` order; a publish failure is recorded on the failing
row only (`mark_failed` — the `attempts` increment lives there) with
infrastructure text that never echoes the payload, and stops the batch (strict
order); backoff grows exponentially and caps; `stop()` ends the loop between
rows, the in-flight row finishing first and the next staying unpublished for
the next run; database outages back the loop off instead of killing it; and
every mark is asserted to commit — the persistence at-least-once depends on.
The `KafkaEnvelopePublisher`'s startup tolerance (a broker that is down at
boot keeps the relay running; publish retries the connection) is pinned
against a monkeypatched aiokafka, the `_safe_error` redaction (raw,
JSON-escaped and whitespace-collapsed echoes) is also exercised directly, and
the `kafka` breaker (Phase 7, ADR 0025) is proven on the publisher and through
the real relay loop: threshold trip, fail-fast with no broker I/O, cooldown
recovery, and the ordered backlog replay once the broker returns.
"""

import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from aiokafka.errors import KafkaConnectionError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.circuit_breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
)
from workers.relay import publisher as publisher_module
from workers.relay.publisher import KafkaEnvelopePublisher
from workers.relay.relay import EnvelopePublisher, OutboxRelay, _safe_error

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)
# A leak test value with JSON-escapable characters (quote, non-ASCII) and a
# whitespace run — the three echo shapes `_safe_error` must redact.
SENTINEL = 'José O"Brien  <ada@example.test>'


def _envelope(n: int = 0) -> dict[str, Any]:
    """A stored-envelope-shaped payload (the relay never re-validates)."""
    return {
        "event_id": f"evt_019a0000-0000-7000-8000-0000000000{n:02d}",
        "event_type": "attendance.checked_in",
        "version": 1,
        "tenant_id": "00000000-0000-4000-8000-00000000000a",
        "actor_id": "00000000-0000-4000-8000-000000000031",
        "occurred_at": "2026-10-16T08:00:00+00:00",
        "data": {
            "attendance_id": "00000000-0000-4000-8000-000000000007",
            "employee_id": f"00000000-0000-4000-8000-0000000000{n:02d}",
            "workday": "2026-10-16",
            "work_mode": "onsite",
            "late": False,
        },
    }


def _run_envelope() -> dict[str, Any]:
    """A run-level payroll envelope — no employee_id (ADR 0012 amendment)."""
    return {
        "event_id": "evt_019a0000-0000-7000-8000-000000000097",
        "event_type": "payroll.calculated",
        "version": 1,
        "tenant_id": "00000000-0000-4000-8000-00000000000a",
        "actor_id": "00000000-0000-4000-8000-000000000031",
        "occurred_at": "2026-10-21T09:00:00+00:00",
        "data": {
            "run_id": "00000000-0000-4000-8000-000000000097",
            "period": "2026-09",
            "employee_count": 12,
        },
    }


def _row(event_id: str, payload: dict[str, Any]) -> EventOutboxRow:
    """A real outbox row — constructed, never flushed (no I/O)."""
    return EventOutboxRow(
        id=uuid7(),
        event_id=event_id,
        tenant_id=UUID("00000000-0000-4000-8000-00000000000a"),
        event_type=str(payload["event_type"]),
        payload=payload,
        occurred_at=NOW,
        created_at=NOW,
    )


class FakeOutboxRepository:
    """In-memory relay repo: marks are recorded; published rows leave the queue."""

    def __init__(self, rows: Sequence[EventOutboxRow]) -> None:
        self.rows = list(rows)
        self.published: list[tuple[UUID, datetime]] = []
        self.failed: list[tuple[UUID, str]] = []

    async def list_unpublished(self, limit: int) -> Sequence[EventOutboxRow]:
        return self.rows[:limit]

    async def mark_published(self, row_id: UUID, at: datetime) -> None:
        self.published.append((row_id, at))
        self.rows = [row for row in self.rows if row.id != row_id]

    async def mark_failed(self, row_id: UUID, error: str) -> None:
        self.failed.append((row_id, error))


class FlakyOutboxRepository(FakeOutboxRepository):
    """Fails the first `failing_polls` polls, like a database outage would."""

    def __init__(self, rows: Sequence[EventOutboxRow], *, failing_polls: int) -> None:
        super().__init__(rows)
        self.failing_polls = failing_polls

    async def list_unpublished(self, limit: int) -> Sequence[EventOutboxRow]:
        if self.failing_polls > 0:
            self.failing_polls -= 1
            raise OperationalError(
                "select * from event_outbox", {}, Exception("db down")
            )
        return await super().list_unpublished(limit)


class RecordingPublisher:
    """Records published envelopes; optional error and per-publish hook."""

    def __init__(
        self,
        error: BaseException | None = None,
        on_publish: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.envelopes: list[dict[str, Any]] = []
        self.error = error
        self.on_publish = on_publish

    async def publish(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)
        if self.on_publish is not None:
            self.on_publish(envelope)
        if self.error is not None:
            raise self.error


class FakeSession:
    """One shared fake session per relay; tests assert that marks commit."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _relay(
    repo: FakeOutboxRepository,
    publisher: EnvelopePublisher,
    *,
    session: FakeSession | None = None,
    batch_size: int = 100,
    poll_seconds: float = 0.001,
    backoff_base: float = 0.5,
    backoff_max: float = 8.0,
) -> OutboxRelay:
    shared = session if session is not None else FakeSession()

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, shared)

    return OutboxRelay(
        session_factory,
        lambda _session: repo,
        publisher,
        batch_size=batch_size,
        poll_seconds=poll_seconds,
        backoff_base=backoff_base,
        backoff_max=backoff_max,
        clock=lambda: NOW,
    )


def _record_pauses(
    monkeypatch: pytest.MonkeyPatch, relay: OutboxRelay, *, stop_after: int
) -> list[float]:
    """Replace the loop's sleep with a recorder; the Nth pause stops the relay."""
    pauses: list[float] = []

    async def fake_pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) >= stop_after:
            relay.stop()

    monkeypatch.setattr(relay, "_pause", fake_pause)
    return pauses


async def test_happy_batch_publishes_and_marks_in_insertion_order() -> None:
    rows = [_row(f"evt_{n}", _envelope(n)) for n in range(3)]
    repo = FakeOutboxRepository(rows)
    publisher = RecordingPublisher()
    session = FakeSession()
    relay = _relay(repo, publisher, session=session)
    publisher.on_publish = lambda _env: (
        relay.stop() if len(publisher.envelopes) == len(rows) else None
    )

    await relay.run()

    assert publisher.envelopes == [row.payload for row in rows]
    assert [row_id for row_id, _ in repo.published] == [row.id for row in rows]
    assert [at for _, at in repo.published] == [NOW, NOW, NOW]  # injected clock
    assert session.commits == len(rows)  # one committed mark per published row
    assert repo.failed == []
    assert repo.rows == []


async def test_empty_backlog_waits_poll_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = FakeOutboxRepository([])
    relay = _relay(repo, RecordingPublisher(), poll_seconds=0.25)
    pauses = _record_pauses(monkeypatch, relay, stop_after=1)

    await relay.run()

    assert pauses == [0.25]
    assert repo.published == []
    assert repo.failed == []


async def test_stop_before_the_first_poll_touches_nothing() -> None:
    row = _row("evt_0", _envelope(0))
    repo = FakeOutboxRepository([row])
    relay = _relay(repo, RecordingPublisher())

    relay.stop()
    await relay.run()

    assert repo.rows == [row]
    assert repo.published == []
    assert repo.failed == []


async def test_stop_between_rows_leaves_the_rest_unpublished() -> None:
    rows = [_row(f"evt_{n}", _envelope(n)) for n in range(2)]
    repo = FakeOutboxRepository(rows)
    publisher = RecordingPublisher()
    relay = _relay(repo, publisher)
    publisher.on_publish = lambda _env: relay.stop()

    await relay.run()

    # The in-flight row finished (published + marked); the next stayed put.
    assert publisher.envelopes == [rows[0].payload]
    assert [row_id for row_id, _ in repo.published] == [rows[0].id]
    assert repo.failed == []
    assert repo.rows == [rows[1]]


async def test_publish_failure_records_payload_free_error_and_stops_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaky = _envelope(0)
    leaky["data"]["note"] = SENTINEL
    failing = _row("evt_fail", leaky)
    next_row = _row("evt_next", _envelope(1))
    repo = FakeOutboxRepository([failing, next_row])
    # The failure is an infrastructure error whose text echoes the payload —
    # exactly the leak `last_error` must never carry (.ai/security.md).
    publisher = RecordingPublisher(
        error=RuntimeError(f"produce failed for {json.dumps(leaky)}")
    )
    session = FakeSession()
    relay = _relay(repo, publisher, session=session, backoff_base=0.5)
    pauses = _record_pauses(monkeypatch, relay, stop_after=1)

    await relay.run()

    # The failing row is bookkept once (`attempts` increments in mark_failed),
    # the batch stopped at it, and the next row was never attempted.
    assert [row_id for row_id, _ in repo.failed] == [failing.id]
    assert repo.published == []
    assert publisher.envelopes == [leaky]
    assert repo.rows == [failing, next_row]  # nothing was dropped
    error = repo.failed[0][1]
    assert error.startswith("RuntimeError: produce failed for ")
    assert SENTINEL not in error
    assert json.dumps(SENTINEL)[1:-1] not in error  # nor its JSON-escaped form
    assert json.dumps(leaky) not in error
    assert session.commits == 1  # the failed mark committed (attempts persisted)
    assert pauses == [0.5]  # first failure: backoff base, then stop


async def test_backoff_grows_between_consecutive_failing_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("evt_fail", _envelope(0))
    repo = FakeOutboxRepository([row])
    publisher = RecordingPublisher(error=RuntimeError("broker unreachable"))
    session = FakeSession()
    relay = _relay(repo, publisher, session=session, backoff_base=0.5, backoff_max=8.0)
    pauses = _record_pauses(monkeypatch, relay, stop_after=3)

    await relay.run()

    assert pauses == [0.5, 1.0, 2.0]
    # Every attempt is recorded; a clean infra message survives unredacted.
    assert {error for _, error in repo.failed} == {"RuntimeError: broker unreachable"}
    assert len(repo.failed) == 3
    assert session.commits == 3  # every failed mark committed
    assert repo.published == []
    assert repo.rows == [row]  # retried, never dropped


async def test_database_outage_backs_off_instead_of_killing_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row("evt_0", _envelope(0))
    repo = FlakyOutboxRepository([row], failing_polls=2)
    publisher = RecordingPublisher()
    session = FakeSession()
    relay = _relay(
        repo, publisher, session=session, poll_seconds=0.25, backoff_base=0.5
    )
    pauses = _record_pauses(monkeypatch, relay, stop_after=3)

    await relay.run()

    # Two outage backoffs, then the drained backlog waits `poll_seconds`.
    assert pauses == [0.5, 1.0, 0.25]
    assert publisher.envelopes == [row.payload]
    assert repo.rows == []
    assert repo.failed == []
    assert session.commits == 1


def test_backoff_caps_at_the_maximum() -> None:
    relay = _relay(
        FakeOutboxRepository([]),
        RecordingPublisher(),
        backoff_base=1.0,
        backoff_max=5.0,
    )

    assert [relay._backoff_seconds(n) for n in range(1, 5)] == [1.0, 2.0, 4.0, 5.0]
    assert relay._backoff_seconds(5000) == 5.0  # bounded exponent, no overflow


def test_safe_error_redacts_raw_escaped_and_collapsed_echoes() -> None:
    envelope = _envelope(0)
    envelope["data"]["note"] = SENTINEL

    raw = _safe_error(RuntimeError(f"send failed: {SENTINEL}"), envelope)
    echoed = _safe_error(RuntimeError(f"send failed: {json.dumps(envelope)}"), envelope)

    assert "José" not in raw  # raw echo, even with the double-space run
    assert 'O"Brien' not in raw
    assert SENTINEL not in raw
    assert SENTINEL not in echoed
    assert json.dumps(SENTINEL)[1:-1] not in echoed  # JSON-escaped echo


def test_safe_error_truncates_long_messages() -> None:
    error = _safe_error(RuntimeError("x" * 1000), _envelope())

    assert error.startswith("RuntimeError: xxx")
    assert error.endswith("…")
    assert len(error) <= len("RuntimeError: ") + 300


def test_safe_error_without_a_message_keeps_the_type() -> None:
    assert _safe_error(RuntimeError(), _envelope()) == "RuntimeError"


def test_invalid_batch_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        _relay(FakeOutboxRepository([]), RecordingPublisher(), batch_size=0)


def test_non_positive_poll_seconds_is_rejected() -> None:
    with pytest.raises(ValueError):
        _relay(FakeOutboxRepository([]), RecordingPublisher(), poll_seconds=0)


def test_invalid_backoff_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        _relay(
            FakeOutboxRepository([]),
            RecordingPublisher(),
            backoff_base=2.0,
            backoff_max=1.0,
        )


class _FakeBroker:
    """Toggleable fake Kafka; the admin/producer fakes read from it."""

    def __init__(self) -> None:
        self.available = False
        self.created_topics: list[str] = []
        self.producer_starts = 0
        self.send_attempts = 0
        self.sent: list[tuple[str, bytes | None, bytes]] = []


def _install_fake_broker(monkeypatch: pytest.MonkeyPatch, broker: _FakeBroker) -> None:
    """Monkeypatch aiokafka's admin/producer with in-memory fakes (no I/O)."""

    class FakeAdminClient:
        def __init__(self, **_kwargs: Any) -> None: ...

        async def start(self) -> None:
            if not broker.available:
                raise KafkaConnectionError("Unable to bootstrap")

        async def create_topics(self, new_topics: Sequence[Any]) -> None:
            broker.created_topics = [topic.name for topic in new_topics]

        async def close(self) -> None: ...

    class FakeProducer:
        def __init__(self, **_kwargs: Any) -> None: ...

        async def start(self) -> None:
            if not broker.available:
                raise KafkaConnectionError("Unable to bootstrap")
            broker.producer_starts += 1

        async def send_and_wait(
            self,
            topic: str,
            *,
            key: bytes | None = None,
            value: bytes | None = None,
        ) -> None:
            broker.send_attempts += 1
            if not broker.available:
                raise KafkaConnectionError("broker connection lost")
            broker.sent.append((topic, key, value or b""))

        async def stop(self) -> None: ...

    monkeypatch.setattr(publisher_module, "AIOKafkaAdminClient", FakeAdminClient)
    monkeypatch.setattr(publisher_module, "AIOKafkaProducer", FakeProducer)


async def test_publisher_tolerates_a_broker_that_is_down_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = _FakeBroker()
    _install_fake_broker(monkeypatch, broker)
    publisher = KafkaEnvelopePublisher(
        bootstrap_servers="127.0.0.1:1", topic="workpulse.events"
    )

    await publisher.start()  # returns — the relay keeps running (AC9)

    assert broker.sent == []
    assert broker.producer_starts == 0


async def test_publisher_retries_the_connection_once_the_broker_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = _FakeBroker()
    _install_fake_broker(monkeypatch, broker)
    publisher = KafkaEnvelopePublisher(
        bootstrap_servers="127.0.0.1:1", topic="workpulse.events"
    )
    await publisher.start()
    envelope = _envelope(0)

    with pytest.raises(KafkaConnectionError):  # the relay bookkeeps this per row
        await publisher.publish(envelope)

    broker.available = True
    await publisher.publish(envelope)
    await publisher.publish(envelope)  # a live producer is reused as-is

    assert broker.created_topics == ["workpulse.events"]
    assert broker.producer_starts == 1
    key = b"00000000-0000-4000-8000-000000000000"  # UTF-8 `data.employee_id`
    value = json.dumps(envelope).encode("utf-8")
    assert broker.sent == [
        ("workpulse.events", key, value),
        ("workpulse.events", key, value),
    ]


async def test_publisher_keys_run_level_events_by_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `employee_id` → the key is UTF-8 `data.run_id` (ADR 0012 amendment)."""
    broker = _FakeBroker()
    broker.available = True
    _install_fake_broker(monkeypatch, broker)
    publisher = KafkaEnvelopePublisher(
        bootstrap_servers="127.0.0.1:1", topic="workpulse.events"
    )
    envelope = _run_envelope()

    await publisher.publish(envelope)

    run_id = envelope["data"]["run_id"]
    assert broker.sent == [
        (
            "workpulse.events",
            run_id.encode("utf-8"),
            json.dumps(envelope).encode("utf-8"),
        )
    ]


# --- kafka circuit breaker (T4, ADR 0025) ------------------------------------


class _FakeClock:
    """Monotonic clock driven explicitly by tests — no wall-clock sleeps."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _kafka_breaker(
    clock: _FakeClock, *, threshold: int = 3, cooldown: float = 30.0
) -> CircuitBreaker:
    return CircuitBreaker(
        "kafka",
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        clock=clock,
    )


def _breaker_publisher(
    broker: _FakeBroker,
    monkeypatch: pytest.MonkeyPatch,
    breaker: CircuitBreaker,
) -> KafkaEnvelopePublisher:
    _install_fake_broker(monkeypatch, broker)
    return KafkaEnvelopePublisher(
        bootstrap_servers="127.0.0.1:1",
        topic="workpulse.events",
        breaker=breaker,
    )


async def test_publisher_breaker_opens_and_fails_fast_without_broker_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-flight outage trips the breaker; the next publish does no I/O."""
    broker = _FakeBroker()
    broker.available = True
    clock = _FakeClock()
    breaker = _kafka_breaker(clock, threshold=3)
    publisher = _breaker_publisher(broker, monkeypatch, breaker)
    envelope = _envelope(0)

    await publisher.publish(envelope)  # warm producer
    assert (broker.producer_starts, len(broker.sent)) == (1, 1)

    broker.available = False
    for attempt in range(3):
        with pytest.raises(KafkaConnectionError):
            await publisher.publish(envelope)
        expected = BreakerState.OPEN if attempt == 2 else BreakerState.CLOSED
        assert breaker.state is expected
    assert breaker.state is BreakerState.OPEN

    # The broker "recovers", but the cooldown is still running: fail fast,
    # without reconnecting or attempting another send.
    broker.available = True
    attempts = broker.send_attempts
    with pytest.raises(CircuitOpenError):
        await publisher.publish(envelope)

    assert broker.send_attempts == attempts
    assert broker.producer_starts == 1
    assert breaker.state is BreakerState.OPEN


async def test_publisher_breaker_recovers_after_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown → one half-open trial → success closes; traffic flows again."""
    broker = _FakeBroker()  # down from the start
    clock = _FakeClock()
    breaker = _kafka_breaker(clock, threshold=1)
    publisher = _breaker_publisher(broker, monkeypatch, breaker)
    envelope = _envelope(0)

    with pytest.raises(KafkaConnectionError):
        await publisher.publish(envelope)
    assert breaker.state is BreakerState.OPEN

    broker.available = True
    clock.advance(29.9)
    with pytest.raises(CircuitOpenError):
        await publisher.publish(envelope)
    assert broker.producer_starts == 0  # no broker I/O while open

    clock.advance(0.1)
    await publisher.publish(envelope)  # the single half-open trial succeeds
    assert breaker.state is BreakerState.CLOSED

    await publisher.publish(envelope)  # closed: ordinary traffic flows
    assert broker.producer_starts == 1
    assert len(broker.sent) == 2


async def test_publisher_failed_trial_reopens_until_the_broker_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed half-open trial re-opens the breaker with a fresh cooldown."""
    broker = _FakeBroker()
    clock = _FakeClock()
    breaker = _kafka_breaker(clock, threshold=1)
    publisher = _breaker_publisher(broker, monkeypatch, breaker)
    envelope = _envelope(0)

    with pytest.raises(KafkaConnectionError):
        await publisher.publish(envelope)

    clock.advance(30.0)
    with pytest.raises(KafkaConnectionError):
        await publisher.publish(envelope)  # trial fails: still down
    assert breaker.state is BreakerState.OPEN

    with pytest.raises(CircuitOpenError):
        await publisher.publish(envelope)  # a fresh cooldown is required

    broker.available = True
    clock.advance(30.0)
    await publisher.publish(envelope)
    assert breaker.state is BreakerState.CLOSED


async def test_relay_backlog_stays_unpublished_while_open_then_recovers_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real relay: fail fast while open, ordered backlog replay on recovery.

    A broker outage trips the breaker after `threshold` row attempts; later
    polls fail fast (rows stay unpublished, the loop keeps backing off and
    bookkeeping). After the broker returns and the cooldown elapses, the single
    trial publishes the backlog head and the rest of the batch follows in row
    order — no payload is dropped, no `event_id` is duplicated, and the API
    write path never depended on the bus (already proven by the relay's
    integration suite).
    """
    rows = [_row(f"evt_{n}", _envelope(n)) for n in range(3)]
    repo = FakeOutboxRepository(rows)
    broker = _FakeBroker()  # down from the start
    clock = _FakeClock()
    breaker = _kafka_breaker(clock, threshold=2)
    publisher = _breaker_publisher(broker, monkeypatch, breaker)
    session = FakeSession()

    # Two connection failures trip the breaker; the third poll fails fast.
    relay = _relay(repo, publisher, session=session, backoff_base=0.5)
    pauses = _record_pauses(monkeypatch, relay, stop_after=3)
    await relay.run()

    assert pauses == [0.5, 1.0, 2.0]
    assert breaker.state is BreakerState.OPEN
    assert repo.published == []
    assert [row_id for row_id, _ in repo.failed] == [rows[0].id] * 3
    assert repo.rows == rows  # nothing dropped
    assert broker.producer_starts == 0
    assert broker.send_attempts == 0
    assert session.commits == 3  # every failed mark committed (`attempts`)

    # Broker back, cooldown still running: still no broker I/O.
    broker.available = True
    still_open = _relay(repo, publisher, session=session, backoff_base=0.5)
    open_pauses = _record_pauses(monkeypatch, still_open, stop_after=1)
    await still_open.run()

    assert open_pauses == [0.5]
    assert broker.producer_starts == 0
    assert broker.send_attempts == 0
    assert repo.published == []

    # Cooldown elapsed: the half-open trial publishes the head, then the rest
    # of the batch follows in row order (strict per-employee ordering).
    clock.advance(30.0)
    recovered = _relay(repo, publisher, session=session, batch_size=3, backoff_base=0.5)
    recovered_pauses = _record_pauses(monkeypatch, recovered, stop_after=1)
    await recovered.run()

    assert recovered_pauses == [0.001]  # backlog drained: wait for new rows
    assert breaker.state is BreakerState.CLOSED
    assert [row_id for row_id, _ in repo.published] == [row.id for row in rows]
    assert repo.rows == []

    expected_keys = [row.payload["data"]["employee_id"].encode("utf-8") for row in rows]
    assert [key for _, key, _ in broker.sent] == expected_keys
    sent_event_ids = [json.loads(value)["event_id"] for _, _, value in broker.sent]
    assert sent_event_ids == [row.payload["event_id"] for row in rows]
    # The duplicate `event_id` window is unchanged: this replay emitted each
    # row exactly once (the at-least-once window is only the crash case).
    assert len(set(sent_event_ids)) == len(rows)
    assert broker.created_topics == ["workpulse.events"]
    assert broker.producer_starts == 1
    assert broker.send_attempts == 3
