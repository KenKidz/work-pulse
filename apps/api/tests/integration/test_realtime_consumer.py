"""T6 integration — the full realtime hop against real brokers (AC1–AC9).

One hop, all real parts: the real `OutboxRelay` drains committed rows to
`kafka-test` (:9093), the real `RealtimeConsumer` (unique group per test,
`latest`) projects `workpulse.events` onto `redis-test` (:6380), and the wall's
own subscriber surfaces the facts — either the raw `RedisPresenceStream`
(`FactFeed`) or the real `RealtimeService.stream` frames for the ≤2s/parity
proof. No fakes: the wire format, the consumer group semantics and the
transient fan-out are the feature (decision 4 of `outbox-relay.md`, ADR 0013).

Test isolation: every consumer group is unique and starts at the topic tip, so
records left by earlier tests (the topic outlives the truncated test database)
are invisible; the relay reads committed rows through its own connections, so
each test commits explicitly where production's `get_session` would.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.api.v1 import realtime as realtime_api
from app.config import get_settings
from app.dependencies import ShortLivedAttendanceBoard
from app.domains.attendance.events import attendance_checked_out
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.realtime.service import RealtimeService
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresOutboxRepository
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.redis import (
    RedisPresencePublisher,
    RedisPresenceStream,
    get_redis,
    tenant_channel,
)
from app.shared.presence import PresenceFact
from tests.conftest import TEST_KAFKA_BOOTSTRAP_SERVERS
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    identity,
)
from workers.realtime.consumer import RealtimeConsumer
from workers.relay.publisher import KafkaEnvelopePublisher
from workers.relay.relay import OutboxRelay

KAFKA_BOOTSTRAP = TEST_KAFKA_BOOTSTRAP_SERVERS
TOPIC = get_settings().kafka_events_topic
SHIFT_PAYLOAD = {
    "name": "Always past",
    "start_time": "00:00",
    "end_time": "23:59",
    "grace_minutes": 0,
}
CLOCK = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)
LATE_MINUTES = 660  # 00:00 + 0 grace → 11:00


def _utc_now() -> datetime:
    """The relay's injected clock (mirrors `workers.relay.main`)."""
    return datetime.now(UTC)


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


async def _seed_world(
    client_factory: ClientFactory,
    session: AsyncSession,
    *,
    late: bool = False,
) -> None:
    """Create the org refs (+ late shift), seed the actor and commit.

    The consumer never reads PostgreSQL, but the hop's relay and the REST
    board do; committing here makes the seed visible to their own sessions.
    """
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        if late:
            shift = await admin.post("/api/v1/shifts", json=SHIFT_PAYLOAD)
            assert shift.status_code == 201
    await _seed_actor(session, refs)
    await session.commit()


# --- the hop ------------------------------------------------------------------


@dataclass
class Hop:
    """The running relay + consumer pair and a direct topic producer."""

    publisher: KafkaEnvelopePublisher

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Publish one envelope as the broker would (redelivery/synthetic)."""
        await self.publisher.publish(envelope)


@asynccontextmanager
async def _hop() -> AsyncIterator[Hop]:
    """Run the production relay + consumer loop against the test brokers.

    The consumer starts first and its `started` event is awaited — the fresh
    group has joined and pinned `latest` — then the relay starts publishing.
    """
    publisher = KafkaEnvelopePublisher(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        topic=TOPIC,
        request_timeout_ms=10_000,
    )
    await publisher.start()

    source = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=f"workpulse-realtime-test-{uuid4()}",
        auto_offset_reset="latest",
        enable_auto_commit=False,
    )
    await source.start()
    consumer = RealtimeConsumer(
        source,
        RedisPresencePublisher(get_redis()),
        poll_seconds=0.05,
        backoff_base=0.05,
        backoff_max=0.2,
    )
    consumer_task = asyncio.create_task(consumer.run())
    relay = OutboxRelay(
        get_session_factory(),
        PostgresOutboxRepository,
        publisher,
        batch_size=100,
        poll_seconds=0.05,
        backoff_base=0.05,
        backoff_max=0.2,
        clock=_utc_now,
    )
    relay_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(30.0):
            await consumer.started.wait()
        relay_task = asyncio.create_task(relay.run())
        yield Hop(publisher=publisher)
    finally:
        if relay_task is not None:
            relay.stop()
            await relay_task
        consumer.stop()
        await consumer_task
        await source.stop()
        await publisher.stop()


# --- the wall's subscribers ---------------------------------------------------


async def _wait_subscribed(channel: str) -> None:
    """Wait until the SUBSCRIBE command has landed on the server.

    An async generator's body (and so `pubsub.subscribe`) only runs at the
    first `__anext__()`; polling `PUBSUB CHANNELS` removes the race between
    "consumer started" and "message published" without a sleep guess.
    """
    for _ in range(200):
        if channel.encode() in await get_redis().pubsub_channels():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subscription to {channel} never registered")


class FactFeed:
    """A wall subscriber: one tenant channel, delivered `PresenceFact`s."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._queue: asyncio.Queue[PresenceFact] = asyncio.Queue()
        self._stream = RedisPresenceStream(get_redis())
        self._generator: AsyncGenerator[PresenceFact, None] | None = None
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "FactFeed":
        self._generator = self._stream.subscribe(self._tenant_id)
        self._task = asyncio.create_task(self._pump())
        await _wait_subscribed(tenant_channel(self._tenant_id))
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        if self._generator is not None:
            await self._generator.aclose()

    async def _pump(self) -> None:
        assert self._generator is not None
        try:
            async for fact in self._generator:
                await self._queue.put(fact)
        except RedisError:
            # A dead bus ends this feed; the test fails on its next timeout.
            pass

    async def next(self, *, timeout: float = 10.0) -> PresenceFact:
        return await asyncio.wait_for(self._queue.get(), timeout)

    async def next_for(
        self, attendance_id: UUID, *, timeout: float = 10.0
    ) -> PresenceFact:
        """The next fact for one attendance record (older backlog skipped)."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    fact = await self.next(timeout=timeout)
                    if fact.attendance_id == attendance_id:
                        return fact
        except TimeoutError:
            raise AssertionError(
                f"no fact for attendance {attendance_id} within {timeout}s"
            ) from None

    async def expect_none(self, *, window: float = 1.0) -> None:
        """Assert no further fact arrives inside `window` seconds."""
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(self._queue.get(), window)


# --- REST helpers -------------------------------------------------------------


async def _rest_row(client_factory: ClientFactory) -> dict[str, Any]:
    """The actor's row in the REST realtime snapshot (the board projection)."""
    async with client_factory() as admin:
        response = await admin.get("/api/v1/realtime/board")
    assert response.status_code == 200
    rows = {row["employee_id"]: row for row in response.json()["presence"]}
    return rows[str(ACTOR)]


def _assert_fact_matches_row(fact: PresenceFact, row: dict[str, Any]) -> None:
    """WS fact → row ≡ the REST snapshot row (identifiers and state only)."""
    assert row["employee_id"] == str(fact.employee_id)
    assert row["status"] == fact.status
    assert row["late"] is fact.late
    assert row["minutes_late"] == fact.minutes_late
    assert datetime.fromisoformat(row["since"]) == fact.since
    assert row["attendance_id"] == str(fact.attendance_id)


# --- tests --------------------------------------------------------------------


async def test_check_in_reaches_the_ws_row_under_2s_and_matches_the_rest_board(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6 — HTTP 201 → WS `presence.upsert` <2s, row ≡ the REST board row."""
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    monkeypatch.setattr(realtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session, late=True)

    service = RealtimeService(
        attendance=ShortLivedAttendanceBoard(get_session_factory()),
        stream=RedisPresenceStream(get_redis()),
    )
    frames = cast(AsyncGenerator[str, None], service.stream(identity(), CLOCK))
    async with _hop(), aclosing(frames):
        pending: asyncio.Future[str] | None = None
        try:
            snapshot = json.loads(await frames.__anext__())
            assert snapshot["type"] == "snapshot"
            pending = asyncio.ensure_future(frames.__anext__())
            # The stream subscribes only on the second `__anext__`; make sure
            # the SUBSCRIBE is on the server before the transition happens.
            await _wait_subscribed(tenant_channel(TENANT_A))

            async with client_factory() as client:
                response = await client.post(
                    "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
                )
            assert response.status_code == 201
            record = response.json()
            assert record["late"] is True
            started = time.monotonic()
            await session.commit()

            frame = json.loads(await asyncio.wait_for(pending, timeout=2.5))
            elapsed = time.monotonic() - started
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending

    assert elapsed < 2.0, f"live delta took {elapsed:.3f}s (> 2s)"
    assert frame["type"] == "presence.upsert"
    assert frame["reason"] == "checked_in"
    row = frame["row"]
    assert row["employee_id"] == str(ACTOR)
    assert row["status"] == "present"
    assert row["late"] is True
    assert row["minutes_late"] == LATE_MINUTES
    assert row["attendance_id"] == record["id"]
    assert datetime.fromisoformat(row["since"]) == datetime.fromisoformat(
        record["checked_in_at"]
    )

    rest_row = await _rest_row(client_factory)
    assert rest_row == row
    assert rest_row["late"] is True and rest_row["minutes_late"] == LATE_MINUTES

    async with client_factory() as admin:
        board = (
            await admin.get(
                "/api/v1/attendance/board",
                params={"date": record["workday"]},
            )
        ).json()
    async with client_factory() as admin:
        snapshot_body = (await admin.get("/api/v1/realtime/board")).json()
    assert snapshot_body["counts"] == board["counts"]


async def test_one_transition_produces_exactly_one_fact(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 — a late check-in's `checked_in` + `late` pair yields one fact."""
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session, late=True)

    async with _hop(), FactFeed(TENANT_A) as feed:
        async with client_factory() as client:
            response = await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        assert response.status_code == 201
        record = response.json()
        await session.commit()

        fact = await feed.next()
        # The `attendance.late` envelope follows in the same transaction and
        # must be filtered — a second frame would violate "at most one delta
        # per transition" (R8). One second is far past the hop's tail.
        await feed.expect_none(window=1.0)

    assert fact.attendance_id == UUID(record["id"])
    assert fact.reason == "checked_in"
    assert fact.status == "present"
    assert fact.late is True
    assert fact.minutes_late == LATE_MINUTES


async def test_check_out_fact_matches_the_rest_board_row(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 — the closed record's fact ≡ the REST board row (late + minutes)."""
    monkeypatch.setattr(attendance_api, "_now", lambda: CLOCK)
    monkeypatch.setattr(realtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session, late=True)

    async with _hop(), FactFeed(TENANT_A) as feed:
        async with client_factory() as client:
            opened = (
                await client.post(
                    "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
                )
            ).json()
            closed_response = await client.post(
                f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
            )
        assert closed_response.status_code == 200
        closed = closed_response.json()
        await session.commit()

        checked_in = await feed.next()
        checked_out = await feed.next()

    assert checked_in.attendance_id == UUID(opened["id"])
    assert checked_out.attendance_id == UUID(opened["id"])
    assert checked_out.reason == "checked_out"
    assert checked_out.status == "checked_out"
    assert checked_out.late is True
    assert checked_out.minutes_late == LATE_MINUTES
    assert datetime.fromisoformat(closed["checked_out_at"]) == checked_out.since

    _assert_fact_matches_row(checked_out, await _rest_row(client_factory))


async def test_redelivering_the_same_envelope_upserts_identically(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """AC7 — at-least-once Kafka redelivery yields an identical full-row fact."""
    await _seed_world(client_factory, session)

    async with _hop() as hop, FactFeed(TENANT_A) as feed:
        async with client_factory() as client:
            response = await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        assert response.status_code == 201
        await session.commit()
        original = await feed.next()

        rows = (
            (await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id)))
            .scalars()
            .all()
        )
        (envelope,) = [row.payload for row in rows]
        await hop.publish(envelope)
        await hop.publish(envelope)
        first_copy = await feed.next()
        second_copy = await feed.next()

        # A pre-extension checked-out envelope (new keys absent) defaults to
        # the values its absence implies (`false` / `null`).
        legacy = attendance_checked_out(
            tenant_id=TENANT_A,
            actor_id=UUID("00000000-0000-4000-8000-000000000031"),
            occurred_at=CLOCK,
            attendance_id=UUID("00000000-0000-4000-8000-0000000000a9"),
            employee_id=ACTOR,
            workday=CLOCK.date(),
            duration_minutes=480,
            late=True,
            minutes_late=30,
        )
        del legacy["data"]["late"]
        del legacy["data"]["minutes_late"]
        await hop.publish(legacy)
        defaulted = await feed.next()

    assert first_copy == original
    assert second_copy == original
    assert defaulted.status == "checked_out"
    assert defaulted.late is False
    assert defaulted.minutes_late is None


async def test_tenant_b_never_sees_tenant_a_deltas(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """AC8 — the envelope's tenant binds the fact to one channel only."""
    await _seed_world(client_factory, session)

    async with _hop(), FactFeed(TENANT_A) as feed_a, FactFeed(TENANT_B) as feed_b:
        async with client_factory() as client:
            response = await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        assert response.status_code == 201
        await session.commit()
        fact = await feed_a.next()
        await feed_b.expect_none(window=1.0)

    assert fact.tenant_id == TENANT_A
    assert fact.employee_id == ACTOR


async def test_degraded_hop_never_blocks_the_write_path(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """AC9 — hop down: writes + board correct, no delta; hop up: next flows."""
    await _seed_world(client_factory, session)

    async with FactFeed(TENANT_A) as feed:
        async with client_factory() as client:
            opened_response = await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
            assert opened_response.status_code == 201
            opened = opened_response.json()
            closed_response = await client.post(
                f"/api/v1/attendance/records/{opened['id']}/check-out", json={}
            )
            assert closed_response.status_code == 200
        await session.commit()

        async with client_factory() as admin:
            board = (
                await admin.get(
                    "/api/v1/attendance/board",
                    params={"date": opened["workday"]},
                )
            ).json()
        assert board["counts"]["present"] == 0
        assert board["counts"]["checked_out"] == 1
        assert len(board["activity"]) == 2
        await feed.expect_none(window=1.0)

        # The hop starts later: the backlog (writes that happened while it was
        # down) drains first; the *next* transition must flow within 2s.
        async with _hop():
            async with client_factory() as client:
                next_response = await client.post(
                    "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
                )
            assert next_response.status_code == 201
            next_record = next_response.json()
            started = time.monotonic()
            await session.commit()
            fact = await feed.next_for(UUID(next_record["id"]), timeout=2.5)
            elapsed = time.monotonic() - started

    assert fact.attendance_id == UUID(next_record["id"])
    assert elapsed < 2.0, f"post-recovery delta took {elapsed:.3f}s (> 2s)"


async def test_bad_topic_messages_never_stall_the_consumer(
    client_factory: ClientFactory,
    session: AsyncSession,
) -> None:
    """Malformed/foreign messages are skipped; the next good transition flows."""
    await _seed_world(client_factory, session)
    foreign = json.dumps({"event_type": "employee.hired", "version": 1, "data": {}})

    async with _hop(), FactFeed(TENANT_A) as feed:
        producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
        await producer.start()
        try:
            for value in (b"this is not json", b"{", foreign.encode("utf-8")):
                await producer.send_and_wait(TOPIC, value=value)
        finally:
            await producer.stop()

        async with client_factory() as client:
            response = await client.post(
                "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
            )
        assert response.status_code == 201
        record = response.json()
        await session.commit()
        fact = await feed.next()
        await feed.expect_none(window=0.5)

    assert fact.attendance_id == UUID(record["id"])
    assert fact.reason == "checked_in"
