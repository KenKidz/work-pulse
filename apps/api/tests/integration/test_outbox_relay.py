"""T8 integration — the real relay against `kafka-test` (AC7–AC10).

Decision 4 of `.ai/features/outbox-relay.md`: the wire format and the
at-least-once behavior *are* the feature, and a fake publisher would prove only
our own loop. So these tests drive the real `OutboxRelay` +
`KafkaEnvelopePublisher` against the throwaway broker (:9093) and consume the
bytes back with a real `AIOKafkaConsumer`:

- **AC7** — rows committed by real check-in/check-out requests arrive on
  `workpulse.events` keyed by `data.employee_id`, their value parses back to
  the stored envelope unchanged (same `event_id`/`occurred_at`/`data` — ADR
  0012), and each row gains `published_at`.
- **AC8** — a late check-in's `checked_in` → `late` pair shares one partition
  and reaches it in builder order (same key → same partition; `ORDER BY id` is
  the order — risk R9).
- **AC9** — an unreachable broker (`127.0.0.1:1`, tight timeouts) leaves every
  row unpublished and bookkept (`attempts`, payload-free `last_error`) while
  the API write path keeps committing; the recovered run publishes the same
  rows with unchanged `event_id`s.
- **AC10** — a fault-injected crash between broker ack and the mark
  re-publishes the identical `event_id` on the next run (at-least-once; the
  documented crash window).

Two harness details matter here: the shared test session
(`client_factory` → `_override_get_session`) flushes but never commits — the
test commits explicitly where production's `get_session` would, because the
relay reads the rows through its own connections. And every consumer is
positioned at the topic end **before** its relay run: the Kafka topic outlives
the truncated test database, so records from earlier tests must be invisible.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from aiokafka import AIOKafkaConsumer
from aiokafka.structs import ConsumerRecord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.config import get_settings
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresOutboxRepository
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from tests.conftest import TEST_KAFKA_BOOTSTRAP_SERVERS
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
)
from workers.relay.publisher import KafkaEnvelopePublisher
from workers.relay.relay import OutboxRelay

KAFKA_BOOTSTRAP = TEST_KAFKA_BOOTSTRAP_SERVERS
TOPIC = get_settings().kafka_events_topic
DEAD_BOOTSTRAP = "127.0.0.1:1"  # nothing listens: connection refused in ms
CLOCK = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)  # the late case's edge clock
SHIFT_PAYLOAD = {
    "name": "Always past",
    "start_time": "00:00",
    "end_time": "23:59",
    "grace_minutes": 0,
}


def _utc_now() -> datetime:
    """The relay's injected clock (mirrors `workers.relay.main`)."""
    return datetime.now(UTC)


# --- fixture helpers ----------------------------------------------------------


async def _seed_actor(session: AsyncSession, refs: tuple[str, str, str]) -> None:
    """Give the default actor an employee row in tenant A (self-service)."""
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        ACTOR,
        employee_code="EMP-00009",
        first_name="Actor",
        last_name="Self",
        email="self@example.test",
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _check_in_pair(client_factory: ClientFactory, session: AsyncSession) -> None:
    """Seed + commit one closed check-in cycle (two outbox rows)."""
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)

    async with client_factory() as client:
        opened_response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert opened_response.status_code == 201
        opened = opened_response.json()
        closed = await client.post(
            f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
        )
        assert closed.status_code == 200

    # The shared test session (`_override_get_session`) stands in for
    # `get_session` but never commits: commit here, as production would, so the
    # relay's own connections can see the rows.
    await session.commit()


# --- world helpers (fresh sessions — the relay commits on other connections) --


async def _load_rows() -> list[EventOutboxRow]:
    async with get_session_factory()() as fresh:
        result = await fresh.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
        return list(result.scalars().all())


async def _unpublished_count() -> int:
    async with get_session_factory()() as fresh:
        stmt = (
            select(func.count())
            .select_from(EventOutboxRow)
            .where(EventOutboxRow.published_at.is_(None))
        )
        return (await fresh.execute(stmt)).scalar_one()


async def _failed_count() -> int:
    async with get_session_factory()() as fresh:
        stmt = (
            select(func.count())
            .select_from(EventOutboxRow)
            .where(EventOutboxRow.last_error.is_not(None))
        )
        return (await fresh.execute(stmt)).scalar_one()


async def _no_unpublished_rows() -> bool:
    return await _unpublished_count() == 0


async def _one_failed_row() -> bool:
    return await _failed_count() >= 1


# --- relay helpers ------------------------------------------------------------


def _make_publisher(
    *, bootstrap_servers: str = KAFKA_BOOTSTRAP, request_timeout_ms: int = 10_000
) -> KafkaEnvelopePublisher:
    return KafkaEnvelopePublisher(
        bootstrap_servers=bootstrap_servers,
        topic=TOPIC,
        request_timeout_ms=request_timeout_ms,
    )


def _make_relay(publisher: KafkaEnvelopePublisher) -> OutboxRelay:
    """The production wiring (ADR 0012), with a tight loop for tests."""
    return OutboxRelay(
        get_session_factory(),
        PostgresOutboxRepository,
        publisher,
        batch_size=100,
        poll_seconds=0.05,
        backoff_base=0.05,
        backoff_max=0.2,
        clock=_utc_now,
    )


async def _wait_until(
    condition: Callable[[], Awaitable[bool]],
    *,
    relay_task: asyncio.Task[None],
    timeout: float,
) -> None:
    """Poll `condition` while the relay runs; a relay crash fails fast."""
    try:
        async with asyncio.timeout(timeout):
            while not await condition():
                if relay_task.done():
                    await relay_task  # re-raise whatever killed the relay
                    raise AssertionError("the relay exited on its own")
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise AssertionError("timed out waiting for the relay") from None


async def _run_until_drained(relay: OutboxRelay, *, timeout: float = 30.0) -> None:
    """Run the real loop until no unpublished rows remain, then stop it."""
    relay_task = asyncio.create_task(relay.run())
    try:
        await _wait_until(_no_unpublished_rows, relay_task=relay_task, timeout=timeout)
    finally:
        relay.stop()
        await relay_task


# --- topic helpers ------------------------------------------------------------


@asynccontextmanager
async def _consumer_at_topic_end() -> AsyncIterator[AIOKafkaConsumer]:
    """A no-group consumer positioned right past the last message.

    The topic outlives the per-test database truncation, so only records
    published after this point are this test's (each run is tailed fresh).
    """
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        auto_offset_reset="latest",
    )
    await consumer.start()
    try:
        await consumer.seek_to_end()
        yield consumer
    finally:
        await consumer.stop()


async def _collect_records(
    consumer: AIOKafkaConsumer, *, count: int, timeout: float = 15.0
) -> list[ConsumerRecord]:
    """The next `count` records; any extra record fails the test as a dup bug."""
    records: list[ConsumerRecord] = []
    deadline = time.monotonic() + timeout
    while len(records) < count and time.monotonic() < deadline:
        batches = await consumer.getmany(timeout_ms=250)
        for batch in batches.values():
            records.extend(batch)
    if len(records) == count:
        # Every record was acked before its mark committed, so by now the log
        # holds exactly this batch: anything more would be an unexpected dup.
        extra = await consumer.getmany(timeout_ms=500)
        assert sum(len(batch) for batch in extra.values()) == 0
    return records


# --- record helpers -----------------------------------------------------------


def _payload_of(record: ConsumerRecord) -> dict[str, Any]:
    """The record's value parsed back — the stored envelope, per ADR 0012."""
    assert record.value is not None
    return cast(dict[str, Any], json.loads(record.value))


def _assert_records_match_rows(
    records: list[ConsumerRecord], rows: list[EventOutboxRow]
) -> None:
    """Each record is one stored envelope, verbatim, keyed by its employee."""
    by_event_id = {row.event_id: row for row in rows}
    assert {_payload_of(record)["event_id"] for record in records} == set(by_event_id)
    for record in records:
        payload = _payload_of(record)
        stored = by_event_id[str(payload["event_id"])]
        assert record.topic == TOPIC
        key = stored.payload["data"]["employee_id"].encode("utf-8")
        assert record.key == key  # `data.employee_id` — the per-employee pin
        # Same `event_id`, same `occurred_at`, same `data`: the published value
        # parses back to the stored envelope — nothing was rebuilt (ADR 0012).
        assert payload == stored.payload


def _payload_strings(value: Any) -> Iterator[str]:
    """Every string value in a JSON-shaped payload, nested or not."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _payload_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _payload_strings(item)


def _assert_no_payload_echo(error: str, payload: dict[str, Any]) -> None:
    """`last_error` is diagnostics: no payload string may appear in it.

    Mirrors the relay's redaction contract: strings shorter than 3 characters
    are never redacted and are skipped here for the same reason.
    """
    for leaf in _payload_strings(payload):
        if len(leaf) < 3:
            continue
        assert leaf not in error
        assert json.dumps(leaf)[1:-1] not in error  # nor its JSON-escaped form


# --- tests --------------------------------------------------------------------


async def test_committed_rows_reach_the_topic_unchanged_and_are_marked(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC7 — rows committed by real check-in/check-out drain to the topic."""
    await _check_in_pair(client_factory, session)

    rows = await _load_rows()
    assert [row.event_type for row in rows] == [
        "attendance.checked_in",
        "attendance.checked_out",
    ]

    publisher = _make_publisher()
    await publisher.start()
    try:
        async with _consumer_at_topic_end() as consumer:
            await _run_until_drained(_make_relay(publisher))
            records = await _collect_records(consumer, count=len(rows))
    finally:
        await publisher.stop()

    assert len(records) == len(rows)
    _assert_records_match_rows(records, rows)

    marked = await _load_rows()
    for row in marked:
        assert row.published_at is not None  # marked only after the broker ack
        assert row.attempts == 1  # every publish attempt: the one that succeeded
        assert row.last_error is None


async def test_late_check_in_pair_lands_in_order_on_one_partition(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8 — `checked_in` then `late`, same key → same partition, in order."""
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        shift = await admin.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
        assert shift.status_code == 201
    await _seed_actor(session, refs)

    # Pin the edge clock past a 00:00 + 0-grace shift: lateness is a fact of
    # the test, not of the wall clock (the T4/T5 pattern).
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
    assert response.status_code == 201
    assert response.json()["late"] is True
    await session.commit()

    rows = await _load_rows()
    assert [row.event_type for row in rows] == [
        "attendance.checked_in",
        "attendance.late",
    ]

    publisher = _make_publisher()
    await publisher.start()
    try:
        async with _consumer_at_topic_end() as consumer:
            await _run_until_drained(_make_relay(publisher))
            records = await _collect_records(consumer, count=2)
    finally:
        await publisher.stop()

    assert len(records) == 2
    first, second = sorted(records, key=lambda record: record.offset)
    # One key (the employee) → one partition; the UUIDv7 poll order preserved
    # the builder order across the relay hop.
    employee_key = rows[0].payload["data"]["employee_id"].encode("utf-8")
    assert [record.key for record in (first, second)] == [employee_key, employee_key]
    assert first.partition == second.partition
    assert first.offset < second.offset
    assert [_payload_of(record)["event_type"] for record in (first, second)] == [
        "attendance.checked_in",
        "attendance.late",
    ]
    assert [_payload_of(record)["event_id"] for record in (first, second)] == [
        row.event_id for row in rows
    ]


async def test_dead_broker_bookkeeps_failures_without_touching_the_write_path(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """AC9 — broker down: rows kept + bookkept, writes unaffected, then replayed."""
    await _check_in_pair(client_factory, session)

    dead = _make_publisher(bootstrap_servers=DEAD_BOOTSTRAP, request_timeout_ms=1000)
    await dead.start()  # tolerated: a broker down at boot must not stop it
    relay = _make_relay(dead)
    relay_task = asyncio.create_task(relay.run())
    try:
        await _wait_until(_one_failed_row, relay_task=relay_task, timeout=60.0)

        # The broker is still down and the relay is still retrying — and the
        # write path never depends on the bus: this check-in commits as usual.
        async with client_factory() as client:
            response = await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        assert response.status_code == 201
        await session.commit()
    finally:
        relay.stop()
        await relay_task
        await dead.stop()

    rows = await _load_rows()
    assert [row.event_type for row in rows] == [
        "attendance.checked_in",
        "attendance.checked_out",
        "attendance.checked_in",
    ]
    # Nothing was published; the batch head is bookkept and the batch stopped
    # at it — the later rows were never attempted (strict order, rule 5).
    assert all(row.published_at is None for row in rows)
    failed, untouched, pending = rows
    assert failed.attempts >= 1
    assert failed.last_error is not None
    assert failed.last_error.startswith("KafkaConnectionError")
    _assert_no_payload_echo(failed.last_error, failed.payload)
    assert (untouched.attempts, untouched.last_error) == (0, None)
    assert (pending.attempts, pending.last_error) == (0, None)

    # Broker back: a later run publishes the same rows, `event_id`s unchanged.
    recovered_ids = {row.event_id for row in rows}
    publisher = _make_publisher()
    await publisher.start()
    try:
        async with _consumer_at_topic_end() as consumer:
            await _run_until_drained(_make_relay(publisher))
            records = await _collect_records(consumer, count=len(rows))
    finally:
        await publisher.stop()

    assert len(records) == len(rows)
    assert {_payload_of(record)["event_id"] for record in records} == recovered_ids

    marked = await _load_rows()
    assert all(row.published_at is not None for row in marked)
    assert marked[0].attempts >= 2  # the recorded failure(s) + the success
    assert (marked[1].attempts, marked[2].attempts) == (1, 1)


async def test_crash_between_broker_ack_and_mark_republishes_the_same_event_id(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10 — the crash window proves at-least-once: duplicate `event_id`."""
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)
    async with client_factory() as client:
        response = await client.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
    assert response.status_code == 201
    await session.commit()
    (row,) = await _load_rows()

    original_mark = PostgresOutboxRepository.mark_published
    crashed = {"armed": True}

    async def _mark_crashes_once(
        self: PostgresOutboxRepository, row_id: UUID, at: datetime
    ) -> None:
        if crashed["armed"]:
            crashed["armed"] = False
            # The broker acked, then the process died before this mark's
            # transaction committed: the attempt left no trace (nothing was
            # written — a real crash would roll the same way).
            raise RuntimeError("simulated crash between broker ack and mark")
        await original_mark(self, row_id, at)

    monkeypatch.setattr(PostgresOutboxRepository, "mark_published", _mark_crashes_once)

    publisher = _make_publisher()
    await publisher.start()
    try:
        async with _consumer_at_topic_end() as consumer:
            # First run: the send is acked, then the mark "crashes" the relay.
            # The await is bounded: the relay is built to retry forever, so a
            # broken broker must fail this test, not hang the whole gate.
            relay_task = asyncio.create_task(_make_relay(publisher).run())
            with pytest.raises(RuntimeError, match="simulated crash"):
                async with asyncio.timeout(30.0):
                    await relay_task

            # The row is unmarked although its first copy is on the topic…
            (after_crash,) = await _load_rows()
            assert after_crash.published_at is None
            assert after_crash.attempts == 0  # the crashed mark rolled back
            first = await _collect_records(consumer, count=1)
            assert [_payload_of(record)["event_id"] for record in first] == [
                row.event_id
            ]

            # …so the next run (the "restart") publishes the same envelope
            # again — the duplicate consumers dedupe on `event_id`.
            await _run_until_drained(_make_relay(publisher))
            second = await _collect_records(consumer, count=1)
    finally:
        await publisher.stop()

    assert len(first) == len(second) == 1
    assert second[0].partition == first[0].partition
    assert second[0].offset > first[0].offset
    assert _payload_of(second[0]) == _payload_of(first[0])
    assert [_payload_of(record)["event_id"] for record in first + second] == [
        row.event_id,
        row.event_id,
    ]

    (recovered,) = await _load_rows()
    assert recovered.published_at is not None
    assert recovered.attempts == 1  # only the committed mark counts (R3)
