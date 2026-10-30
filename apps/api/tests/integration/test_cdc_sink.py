"""T4 real-broker + real-store proofs — the CDC sink end to end (ADR 0024).

Every part except the assertion helpers is real: a dedicated `kafka-test` topic
carries Debezium-shaped envelopes, the actual `CdcSinkConsumer` runs with a
throwaway group, and `ClickhouseCdcWriter` lands the rows in the D2 shadow
database (`workpulse_test_cdc`) of the throwaway ClickHouse. Each test uses its
own topic and fixed ids, so the shared broker and the shadow projection cannot
leak between tests; the fixture drops/recreates the shadow tables around every
test.

Covered: the five projection tables land exact rows (AC 1 shape); a redelivered
change with the same commit timestamp converges (AC 7); delete/truncate ops
remove nothing (AC 6); the readiness fence holds through snapshot rows and flips
on the `last` row (AC 5 mechanics); pending leave writes no fact and an
`included_in_payroll` overtime row projects as `hr_verified` (D6).

Readiness is deterministic: producer writes are awaited and committed state is
polled with a bounded wait — no fixed sleeps (`.ai/testing.md`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from clickhouse_connect.driver.client import Client

from app.config import get_settings
from app.infrastructure.clickhouse.cdc_projection import ClickhouseCdcWriter
from app.infrastructure.clickhouse.schema import SCHEMA_TABLES
from tests.conftest import TEST_KAFKA_BOOTSTRAP_SERVERS
from workers.cdc_sink.consumer import CdcSinkConsumer
from workers.cdc_sink.main import _is_fresh_group, _warm_leave_types
from workers.cdc_sink.mapping import LeaveTypeBook, decode_leave_type

KAFKA_BOOTSTRAP = TEST_KAFKA_BOOTSTRAP_SERVERS
TENANT = UUID("00000000-0000-4000-8000-00000000000a")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
EMPLOYEE_B = UUID("00000000-0000-4000-8000-000000000002")
EMPLOYEE_C = UUID("00000000-0000-4000-8000-000000000003")
DEPARTMENT = UUID("00000000-0000-4000-8000-0000000000d1")
POSITION = UUID("00000000-0000-4000-8000-0000000000e1")
LOCATION = UUID("00000000-0000-4000-8000-0000000000f1")
OVERTIME = UUID("00000000-0000-4000-8000-0000000000c1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c3")
LEAVE = UUID("00000000-0000-4000-8000-0000000000c4")
LEAVE_TYPE = UUID("00000000-0000-4000-8000-0000000000c5")

EPOCH = date(1970, 1, 1)
WORKDAY = date(2026, 10, 23)
HIRED_AT = date(2026, 1, 5)
EFFECTIVE_FROM = date(2026, 6, 1)
COMMIT_TS = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
COMMIT_MS = int(COMMIT_TS.timestamp() * 1000)


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def writer() -> Iterator[ClickhouseCdcWriter]:
    target = get_settings().cdc_projection_database
    assert target == "workpulse_test_cdc"
    sink = ClickhouseCdcWriter.connect(target)
    _drop_tables(sink._client)
    sink.apply_schema()
    try:
        yield sink
    finally:
        _drop_tables(sink._client)
        sink.close()


def _drop_tables(client: Client) -> None:
    for table in SCHEMA_TABLES:
        client.command(f"DROP TABLE IF EXISTS {table}")


@asynccontextmanager
async def _topic() -> AsyncIterator[str]:
    """A dedicated single-partition topic so tests never share history."""
    name = f"workpulse-cdc-test-{uuid4().hex}"
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP)
    await admin.start()
    try:
        await admin.create_topics(
            [NewTopic(name=name, num_partitions=1, replication_factor=1)]
        )
        yield name
    finally:
        try:
            await admin.delete_topics([name])
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        await admin.close()


@asynccontextmanager
async def _producer() -> AsyncIterator[AIOKafkaProducer]:
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP)
    await producer.start()
    try:
        yield producer
    finally:
        await producer.stop()


@asynccontextmanager
async def _sink(
    topic: str,
    writer: ClickhouseCdcWriter,
    *,
    fresh_group: bool,
    leave_types: LeaveTypeBook | None = None,
) -> AsyncIterator[CdcSinkConsumer]:
    source = AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=f"workpulse-cdc-sink-test-{uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    consumer = CdcSinkConsumer(
        source,
        writer,
        poll_seconds=0.05,
        backoff_base=0.05,
        backoff_max=0.2,
        fresh_group=fresh_group,
        leave_types=leave_types,
    )
    await source.start()
    task = asyncio.create_task(consumer.run())
    try:
        async with asyncio.timeout(30.0):
            await consumer.started.wait()
        yield consumer
    finally:
        consumer.stop()
        await task
        await source.stop()


async def _publish(
    producer: AIOKafkaProducer, topic: str, *values: dict[str, Any]
) -> None:
    for value in values:
        await producer.send_and_wait(topic, value=json.dumps(value).encode("utf-8"))


async def _eventually[T](
    probe: Callable[[], T | None],
    *,
    description: str,
    timeout: float = 20.0,
) -> T:
    """Poll committed store state under one bounded wait (no fixed sleeps)."""

    async def poll() -> T:
        while True:
            result = probe()
            if result is not None:
                return result
            await asyncio.sleep(0.02)

    try:
        return await asyncio.wait_for(poll(), timeout)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {description}") from None


def _row(client: Client, sql: str, **parameters: Any) -> tuple[Any, ...] | None:
    rows = client.query(sql, parameters=parameters).result_rows
    return tuple(rows[0]) if rows else None


def _ready(writer: ClickhouseCdcWriter) -> int | None:
    state = writer.readiness()
    return state[0] if state is not None else None


# --- envelope builders --------------------------------------------------------


def _epoch_days(value: date) -> int:
    return (value - EPOCH).days


def _envelope(
    table: str,
    after: dict[str, Any] | None,
    *,
    op: str = "c",
    snapshot: str = "false",
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
            "db": "workpulse_test",
            "schema": "public",
            "table": table,
        },
        "op": op,
        "ts_ms": ts_ms + 1,
        "transaction": None,
    }


def _employee(employee_id: UUID = EMPLOYEE) -> dict[str, Any]:
    return {
        "id": str(employee_id),
        "tenant_id": str(TENANT),
        "hired_at": _epoch_days(HIRED_AT),
        "terminated_at": None,
    }


def _assignment() -> dict[str, Any]:
    return {
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "department_id": str(DEPARTMENT),
        "position_id": str(POSITION),
        "location_id": str(LOCATION),
        "effective_from": _epoch_days(EFFECTIVE_FROM),
        "effective_to": None,
    }


def _attendance() -> dict[str, Any]:
    return {
        "id": str(ATTENDANCE),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "workday": _epoch_days(WORKDAY),
        "duration_minutes": 468,
        "late": True,
        "minutes_late": 12,
    }


def _overtime(status: str = "included_in_payroll") -> dict[str, Any]:
    return {
        "id": str(OVERTIME),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "work_date": _epoch_days(WORKDAY),
        "hours": "2.50",
        "status": status,
    }


def _leave(status: str) -> dict[str, Any]:
    return {
        "id": str(LEAVE),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "leave_type_id": str(LEAVE_TYPE),
        "start_date": _epoch_days(WORKDAY),
        "days": 2,
        "status": status,
    }


def _leave_type() -> dict[str, Any]:
    return {"id": str(LEAVE_TYPE), "tenant_id": str(TENANT), "code": "annual"}


# --- tests --------------------------------------------------------------------


async def test_snapshot_lands_every_projection_row_and_flips_readiness(
    writer: ClickhouseCdcWriter,
) -> None:
    async with _topic() as topic, _producer() as producer:
        async with _sink(topic, writer, fresh_group=True):
            await _publish(
                producer,
                topic,
                _envelope("employees", _employee(), op="r", snapshot="true"),
                _envelope("employments", _assignment(), op="r", snapshot="true"),
                _envelope("attendance_records", _attendance(), op="r", snapshot="true"),
                _envelope("overtime_requests", _overtime(), op="r", snapshot="true"),
                _envelope("leave_types", _leave_type(), op="r", snapshot="true"),
                _envelope(
                    "leave_requests", _leave("approved"), op="r", snapshot="true"
                ),
            )

            employee = await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT employee_id, hired_at FROM dim_employee FINAL "
                    "WHERE employee_id = {id:UUID}",
                    id=EMPLOYEE,
                ),
                description="the employee dimension row",
            )
            assert employee == (EMPLOYEE, HIRED_AT)
            # Snapshot rows are still landing: the fence must hold.
            assert _ready(writer) == 0

            assignment = await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT effective_from, department_id FROM dim_assignment FINAL "
                    "WHERE employee_id = {id:UUID}",
                    id=EMPLOYEE,
                ),
                description="the assignment interval",
            )
            assert assignment == (EFFECTIVE_FROM, DEPARTMENT)

            attendance = await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT duration_minutes, late, minutes_late, department_id "
                    "FROM fact_attendance FINAL WHERE attendance_id = {id:UUID}",
                    id=ATTENDANCE,
                ),
                description="the attendance fact",
            )
            assert attendance == (468, True, 12, None)

            overtime = await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT hours, status FROM fact_overtime FINAL "
                    "WHERE overtime_id = {id:UUID}",
                    id=OVERTIME,
                ),
                description="the overtime fact",
            )
            assert overtime == (2.5, "hr_verified")

            leave = await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT leave_type_code, approved_day_count FROM fact_leave FINAL "
                    "WHERE leave_request_id = {id:UUID}",
                    id=LEAVE,
                ),
                description="the leave fact",
            )
            assert leave == ("annual", 2)

            # The final snapshot row is the completion signal.
            await _publish(
                producer,
                topic,
                _envelope(
                    "leave_types",
                    {**_leave_type(), "code": "annual"},
                    op="r",
                    snapshot="last",
                    ts_ms=COMMIT_MS + 1,
                ),
            )
            await _eventually(
                lambda: _ready(writer) == 1 or None,
                description="the readiness flip",
            )


async def test_redelivery_with_the_same_commit_timestamp_converges(
    writer: ClickhouseCdcWriter,
) -> None:
    async with _topic() as topic, _producer() as producer:
        async with _sink(topic, writer, fresh_group=True):
            await _publish(
                producer,
                topic,
                _envelope("overtime_requests", _overtime()),
                _envelope("overtime_requests", _overtime()),
                # Ordered marker: the duplicate has been handled once the next
                # row (a later commit timestamp) is visible.
                _envelope("employees", _employee(EMPLOYEE_B), ts_ms=COMMIT_MS + 1000),
            )
            await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT employee_id FROM dim_employee FINAL "
                    "WHERE employee_id = {id:UUID}",
                    id=EMPLOYEE_B,
                ),
                description="the marker employee",
            )

            count = _row(
                writer._client,
                "SELECT count(), any(status) FROM fact_overtime FINAL "
                "WHERE overtime_id = {id:UUID}",
                id=OVERTIME,
            )
            assert count == (1, "hr_verified")


async def test_delete_and_truncate_never_remove_a_projection_row(
    writer: ClickhouseCdcWriter,
) -> None:
    async with _topic() as topic, _producer() as producer:
        async with _sink(topic, writer, fresh_group=True):
            await _publish(producer, topic, _envelope("employees", _employee()))
            await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT employee_id FROM dim_employee FINAL "
                    "WHERE employee_id = {id:UUID}",
                    id=EMPLOYEE,
                ),
                description="the employee row",
            )

            await _publish(
                producer,
                topic,
                _envelope(
                    "employees",
                    None,
                    op="d",
                    before={"id": str(EMPLOYEE)},
                    ts_ms=COMMIT_MS + 1000,
                ),
                _envelope("employees", None, op="t", ts_ms=COMMIT_MS + 2000),
                _envelope("employees", _employee(EMPLOYEE_C), ts_ms=COMMIT_MS + 3000),
            )
            await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT employee_id FROM dim_employee FINAL "
                    "WHERE employee_id = {id:UUID}",
                    id=EMPLOYEE_C,
                ),
                description="the post-delete marker",
            )

            assert _row(
                writer._client,
                "SELECT employee_id FROM dim_employee FINAL "
                "WHERE employee_id = {id:UUID}",
                id=EMPLOYEE,
            ) == (EMPLOYEE,)


async def test_pending_leave_writes_no_fact_and_approval_updates_it(
    writer: ClickhouseCdcWriter,
) -> None:
    async with _topic() as topic, _producer() as producer:
        async with _sink(topic, writer, fresh_group=True) as consumer:
            consumer._leave_types.apply(
                # The warm-up replay in production does this; tests seed the
                # book directly so the reference is present on the first row.
                decode_leave_type(_leave_type())
            )
            await _publish(
                producer,
                topic,
                _envelope("leave_requests", _leave("pending")),
                _envelope("employees", _employee(EMPLOYEE_B), ts_ms=COMMIT_MS + 1000),
            )
            await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT employee_id FROM dim_employee FINAL "
                    "WHERE employee_id = {id:UUID}",
                    id=EMPLOYEE_B,
                ),
                description="the pending-leave marker",
            )
            assert _row(
                writer._client,
                "SELECT count() FROM fact_leave FINAL "
                "WHERE leave_request_id = {id:UUID}",
                id=LEAVE,
            ) == (0,)

            await _publish(
                producer,
                topic,
                _envelope(
                    "leave_requests",
                    _leave("approved"),
                    op="u",
                    before={"id": str(LEAVE)},
                    ts_ms=COMMIT_MS + 2000,
                ),
            )
            fact = await _eventually(
                lambda: _row(
                    writer._client,
                    "SELECT leave_type_code, approved_day_count FROM fact_leave FINAL "
                    "WHERE leave_request_id = {id:UUID}",
                    id=LEAVE,
                ),
                description="the approved leave fact",
            )
            assert fact == ("annual", 2)


async def test_leave_type_warmup_replays_the_reference_topic() -> None:
    async with _topic() as topic, _producer() as producer:
        await _publish(
            producer,
            topic,
            _envelope("leave_types", _leave_type(), op="r", snapshot="last"),
        )
        book = LeaveTypeBook()
        await _warm_leave_types(get_settings(), book, topic)
        assert book.resolve(TENANT, LEAVE_TYPE) == "annual"


async def test_fresh_group_detection_uses_committed_offsets() -> None:
    async with _topic() as topic, _producer() as producer:
        source = AIOKafkaConsumer(
            topic,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=f"workpulse-cdc-fresh-test-{uuid4()}",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await source.start()
        try:
            assert await _is_fresh_group(source, (topic,)) is True

            await _publish(producer, topic, _envelope("employees", _employee()))
            batches = None
            async with asyncio.timeout(20.0):
                while not batches:
                    batches = await source.getmany(timeout_ms=200)
            await source.commit()

            assert await _is_fresh_group(source, (topic,)) is False
        finally:
            await source.stop()
