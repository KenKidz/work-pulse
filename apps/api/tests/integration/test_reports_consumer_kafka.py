"""T6 real-broker proofs — the reports projection worker end to end (ADR 0019).

Every part except the assertion helpers is real: a dedicated `kafka-test` topic
carries the v1 envelopes, the actual `ReportsConsumer` runs with its stable
`earliest` group, and `ClickhouseProjectionWriter` writes the real throwaway
ClickHouse (`clickhouse-test`, :8124). Each test uses its own tenant and topic,
so the shared broker's history and the projection tables cannot leak between
tests; the ClickHouse fixture drops/recreates the schema around every test.

Covered (`.ai/features/analytics-reports.md` → acceptance criteria):

- a produced event slice projects the exact rows/aggregates on every report;
- whole-stream redelivery (duplicate `event_id`s) leaves aggregates unchanged;
- an `hr_verified → rejected` un-verification removes the hours;
- truncate + replay from `earliest` reproduces the aggregates, with the
  `projection_state.ready` rebuild fence toggled by the test;
- topic creation pins `retention.ms` to the maximum report span plus margin;
- pre-extension envelopes degrade honestly (`unknown` tenure, carried-forward
  position/location);
- malformed/foreign messages are skipped without stalling the worker.

Readiness is deterministic: the consumer's `started` event is awaited before
anything is produced, and committed ClickHouse state is awaited with a bounded
poll — no fixed "give the worker a second" sleeps (`.ai/testing.md`).
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.admin.config_resource import ConfigResource, ConfigResourceType
from clickhouse_connect.driver.client import Client

from app.domains.attendance.events import (
    attendance_checked_in,
    attendance_checked_out,
)
from app.domains.employees.events import employee_created, employee_transferred
from app.domains.leave.events import leave_approved
from app.domains.overtime.events import (
    overtime_approved,
    overtime_rejected,
    overtime_requested,
    overtime_verified,
)
from app.domains.reports.exceptions import ReportsUnavailableError
from app.domains.reports.schemas import (
    AttendanceAggregateRow,
    HeadcountAggregateRow,
    LeaveAggregateRow,
    OvertimeAggregateRow,
    ReportQuery,
)
from app.infrastructure.clickhouse.client import (
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.projection import ClickhouseProjectionWriter
from app.infrastructure.clickhouse.reports import ClickhouseReportsRepository
from app.infrastructure.clickhouse.schema import SCHEMA_TABLES, apply_clickhouse_schema
from tests.conftest import TEST_KAFKA_BOOTSTRAP_SERVERS
from workers.relay.publisher import REPORT_RETENTION_MS, KafkaEnvelopePublisher
from workers.reports.consumer import ReportsConsumer

KAFKA_BOOTSTRAP = TEST_KAFKA_BOOTSTRAP_SERVERS
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
HIRED_AT = date(2026, 1, 5)
TRANSFER_DATE = date(2026, 3, 1)
WORK_DATE = date(2026, 2, 10)
WORKDAY = date(2026, 2, 11)
LEAVE_DAY = date(2026, 2, 12)
FEB_FROM = date(2026, 2, 1)
FEB_TO = date(2026, 2, 28)
SENTINEL = "Zorblax Qqweird pii-unique@example.test"


@pytest.fixture
def clickhouse() -> Iterator[Client]:
    client = get_clickhouse_client()
    _drop_tables(client)
    apply_clickhouse_schema(client)
    try:
        yield client
    finally:
        _drop_tables(client)
        dispose_clickhouse()


def _drop_tables(client: Client) -> None:
    for table in SCHEMA_TABLES:
        client.command(f"DROP TABLE IF EXISTS {table}")


# --- fixture helpers ----------------------------------------------------------


@asynccontextmanager
async def _topic() -> AsyncIterator[str]:
    """A dedicated single-partition topic so tests never share history."""
    name = f"workpulse-reports-test-{uuid4().hex}"
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
async def _consumer(
    topic: str, clickhouse: Client, *, group_id: str | None = None
) -> AsyncIterator[ReportsConsumer]:
    """Run the production consumer loop against the real broker + store."""
    source = AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=group_id or f"workpulse-reports-test-{uuid4()}",
        # The production group's position: a first start replays the retained
        # history (ADR 0019 decision 6).
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    consumer = ReportsConsumer(
        source,
        ClickhouseProjectionWriter(clickhouse),
        poll_seconds=0.05,
        backoff_base=0.05,
        backoff_max=0.2,
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
    producer: AIOKafkaProducer, topic: str, *values: dict[str, Any] | bytes
) -> None:
    for value in values:
        payload = (
            value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        )
        await producer.send_and_wait(topic, value=payload)


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


async def _await_marker(
    writer: ClickhouseProjectionWriter,
    tenant_id: UUID,
    leave_request_id: UUID,
) -> None:
    """Block until the last-produced leave marker is projected (ordered topic)."""
    await _eventually(
        lambda: writer.get_leave(tenant_id, leave_request_id),
        description="the marker leave event",
    )


# --- envelope builders --------------------------------------------------------


def _created(
    tenant_id: UUID,
    employee_id: UUID,
    *,
    department_id: UUID,
    position_id: UUID,
    location_id: UUID,
    hired_at: date | None = HIRED_AT,
) -> dict[str, Any]:
    return employee_created(
        tenant_id=tenant_id,
        actor_id=uuid4(),
        occurred_at=NOW,
        employee_id=employee_id,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        employment_type="full_time",
        hired_at=hired_at,
    )


def _overtime_events(
    tenant_id: UUID,
    employee_id: UUID,
    overtime_id: UUID,
    *,
    work_date: date = WORK_DATE,
    hours: Decimal = Decimal("2.50"),
) -> list[dict[str, Any]]:
    actor = uuid4()
    return [
        overtime_requested(
            tenant_id=tenant_id,
            actor_id=actor,
            occurred_at=NOW,
            overtime_id=overtime_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=hours,
        ),
        overtime_approved(
            tenant_id=tenant_id,
            actor_id=actor,
            occurred_at=NOW,
            overtime_id=overtime_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=hours,
            approved_by=actor,
        ),
        overtime_verified(
            tenant_id=tenant_id,
            actor_id=actor,
            occurred_at=NOW,
            overtime_id=overtime_id,
            employee_id=employee_id,
            work_date=work_date,
            hours=hours,
            verified_by=actor,
        ),
    ]


def _attendance_events(
    tenant_id: UUID, employee_id: UUID, attendance_id: UUID
) -> list[dict[str, Any]]:
    actor = uuid4()
    return [
        attendance_checked_in(
            tenant_id=tenant_id,
            actor_id=actor,
            occurred_at=NOW,
            attendance_id=attendance_id,
            employee_id=employee_id,
            workday=WORKDAY,
            work_mode="onsite",
            late=True,
            minutes_late=15,
        ),
        attendance_checked_out(
            tenant_id=tenant_id,
            actor_id=actor,
            occurred_at=NOW,
            attendance_id=attendance_id,
            employee_id=employee_id,
            workday=WORKDAY,
            duration_minutes=450,
            late=True,
            minutes_late=15,
        ),
    ]


def _leave(
    tenant_id: UUID, employee_id: UUID, leave_request_id: UUID
) -> dict[str, Any]:
    return leave_approved(
        tenant_id=tenant_id,
        actor_id=uuid4(),
        occurred_at=NOW,
        leave_request_id=leave_request_id,
        employee_id=employee_id,
        leave_type_code="annual",
        start_date=LEAVE_DAY,
        end_date=LEAVE_DAY,
        days=2,
        decided_by=uuid4(),
    )


# --- report reads -------------------------------------------------------------


def _query(
    tenant_id: UUID, *, group_by: tuple[str, ...] = ("department",)
) -> ReportQuery:
    return ReportQuery(
        tenant_id=tenant_id,
        start=FEB_FROM,
        end=FEB_TO,
        interval="month",
        group_by=group_by,
    )


def _overtime_rows(clickhouse: Client, tenant_id: UUID) -> list[OvertimeAggregateRow]:
    return list(
        ClickhouseReportsRepository(clickhouse).overtime_aggregates(_query(tenant_id))
    )


# --- AC 1/3/4: exact projection ------------------------------------------------


async def test_event_slice_projects_exact_rows_for_all_four_reports(
    clickhouse: Client,
) -> None:
    tenant_id = uuid4()
    employee_id = uuid4()
    department_id = uuid4()
    position_id = uuid4()
    location_id = uuid4()
    overtime_id = uuid4()
    marker_overtime_id = uuid4()
    attendance_id = uuid4()
    leave_request_id = uuid4()
    marker_leave_id = uuid4()

    events = [
        _created(
            tenant_id,
            employee_id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
        ),
        *_overtime_events(tenant_id, employee_id, overtime_id),
        *_attendance_events(tenant_id, employee_id, attendance_id),
        _leave(tenant_id, employee_id, leave_request_id),
        # A second verified claim on another workday: part of the exact
        # aggregate, and the ordered marker that everything before it landed.
        *_overtime_events(
            tenant_id,
            employee_id,
            marker_overtime_id,
            work_date=date(2026, 2, 13),
            hours=Decimal("1.25"),
        ),
        _leave(tenant_id, employee_id, marker_leave_id),
    ]

    async with _topic() as topic, _producer() as producer:
        async with _consumer(topic, clickhouse):
            await _publish(producer, topic, *events)
            writer = ClickhouseProjectionWriter(clickhouse)
            # The marker leave is last: once it is projected, every earlier
            # event on the single partition has been folded and written.
            await _await_marker(writer, tenant_id, marker_leave_id)

    overtime = writer.get_overtime(tenant_id, overtime_id)
    assert overtime is not None
    assert overtime.status == "hr_verified"
    assert overtime.hours == Decimal("2.50")
    assert overtime.department_id == department_id
    assert overtime.position_id == position_id
    assert overtime.location_id == location_id
    assert overtime.hired_at == HIRED_AT

    attendance = writer.get_attendance(tenant_id, attendance_id)
    assert attendance is not None
    assert attendance.duration_minutes == 450
    assert attendance.late is True
    assert attendance.minutes_late == 15
    assert attendance.department_id == department_id

    leave = writer.get_leave(tenant_id, leave_request_id)
    assert leave is not None
    assert leave.approved_day_count == 2
    assert leave.leave_type_code == "annual"
    assert leave.department_id == department_id

    [overtime_row] = _overtime_rows(clickhouse, tenant_id)
    assert overtime_row.bucket == FEB_FROM
    assert overtime_row.keys == (str(department_id),)
    assert overtime_row.verified_hours == Decimal("3.75")
    assert overtime_row.verified_request_count == 2
    assert overtime_row.verified_day_count == 2
    assert overtime_row.active_employee_count == 1

    [attendance_row] = ClickhouseReportsRepository(clickhouse).attendance_aggregates(
        _query(tenant_id)
    )
    assert attendance_row.checkin_count == 1
    assert attendance_row.worked_day_count == 1
    assert attendance_row.worked_minutes == 450
    assert attendance_row.late_count == 1
    assert attendance_row.minutes_late_total == 15

    [leave_row] = ClickhouseReportsRepository(clickhouse).leave_aggregates(
        _query(tenant_id, group_by=("leave_type",))
    )
    assert leave_row.keys == ("annual",)
    assert leave_row.approved_day_count == 4  # two requests × two days
    assert leave_row.approved_request_count == 2

    [headcount_row] = ClickhouseReportsRepository(clickhouse).headcount_aggregates(
        _query(tenant_id)
    )
    assert headcount_row.headcount == 1
    assert headcount_row.joiners == 0
    assert headcount_row.leavers == 0


async def test_redelivered_stream_leaves_aggregates_unchanged(
    clickhouse: Client,
) -> None:
    tenant_id = uuid4()
    employee_id = uuid4()
    department_id = uuid4()
    overtime_id = uuid4()
    attendance_id = uuid4()
    leave_request_id = uuid4()
    marker_leave_id = uuid4()

    events = [
        _created(
            tenant_id,
            employee_id,
            department_id=department_id,
            position_id=uuid4(),
            location_id=uuid4(),
        ),
        *_overtime_events(tenant_id, employee_id, overtime_id),
        *_attendance_events(tenant_id, employee_id, attendance_id),
        _leave(tenant_id, employee_id, leave_request_id),
    ]

    async with _topic() as topic, _producer() as producer:
        async with _consumer(topic, clickhouse):
            # The whole stream twice: identical `event_id`s are the
            # at-least-once redelivery shape ADR 0019 decision 4 absorbs.
            await _publish(producer, topic, *events, *events)
            await _publish(
                producer, topic, _leave(tenant_id, employee_id, marker_leave_id)
            )
            writer = ClickhouseProjectionWriter(clickhouse)
            await _await_marker(writer, tenant_id, marker_leave_id)

    # One deduped row per entity, never one per delivery.
    assert (
        clickhouse.command(
            f"SELECT count() FROM fact_overtime FINAL WHERE tenant_id = '{tenant_id}'"
        )
        == 1
    )
    assert (
        clickhouse.command(
            f"SELECT count() FROM fact_attendance FINAL WHERE tenant_id = '{tenant_id}'"
        )
        == 1
    )
    assert (
        clickhouse.command(
            f"SELECT count() FROM fact_leave FINAL WHERE tenant_id = '{tenant_id}'"
        )
        == 2
    )

    [overtime_row] = _overtime_rows(clickhouse, tenant_id)
    assert overtime_row.verified_hours == Decimal("2.50")
    assert overtime_row.verified_request_count == 1
    assert overtime_row.verified_day_count == 1

    [attendance_row] = ClickhouseReportsRepository(clickhouse).attendance_aggregates(
        _query(tenant_id)
    )
    assert attendance_row.worked_minutes == 450
    assert attendance_row.checkin_count == 1


async def test_un_verification_removes_the_hours(clickhouse: Client) -> None:
    tenant_id = uuid4()
    employee_id = uuid4()
    overtime_id = uuid4()
    marker_leave_id = uuid4()

    events = [
        _created(
            tenant_id,
            employee_id,
            department_id=uuid4(),
            position_id=uuid4(),
            location_id=uuid4(),
        ),
        *_overtime_events(tenant_id, employee_id, overtime_id),
        overtime_rejected(
            tenant_id=tenant_id,
            actor_id=uuid4(),
            occurred_at=NOW,
            overtime_id=overtime_id,
            employee_id=employee_id,
            work_date=WORK_DATE,
            hours=Decimal("2.50"),
            rejected_by=uuid4(),
            rejected_from="hr_verified",
        ),
        _leave(tenant_id, employee_id, marker_leave_id),
    ]

    async with _topic() as topic, _producer() as producer:
        async with _consumer(topic, clickhouse):
            await _publish(producer, topic, *events)
            writer = ClickhouseProjectionWriter(clickhouse)
            await _await_marker(writer, tenant_id, marker_leave_id)

    overtime = writer.get_overtime(tenant_id, overtime_id)
    assert overtime is not None
    assert overtime.status == "rejected"
    # Verified hours are counted only while the row is `hr_verified` (spec
    # rule 5): the un-verification drops the claim out of every bucket.
    assert _overtime_rows(clickhouse, tenant_id) == []


# --- AC 10: rebuild ------------------------------------------------------------


async def test_truncate_and_replay_reproduces_the_aggregates(
    clickhouse: Client,
) -> None:
    tenant_id = uuid4()
    employee_id = uuid4()
    department_id = uuid4()
    overtime_id = uuid4()
    attendance_id = uuid4()
    leave_request_id = uuid4()
    marker_leave_id = uuid4()

    events = [
        _created(
            tenant_id,
            employee_id,
            department_id=department_id,
            position_id=uuid4(),
            location_id=uuid4(),
        ),
        *_overtime_events(tenant_id, employee_id, overtime_id),
        *_attendance_events(tenant_id, employee_id, attendance_id),
        _leave(tenant_id, employee_id, leave_request_id),
        _leave(tenant_id, employee_id, marker_leave_id),
    ]

    async with _topic() as topic, _producer() as producer:
        await _publish(producer, topic, *events)

        async with _consumer(topic, clickhouse):
            writer = ClickhouseProjectionWriter(clickhouse)
            await _await_marker(writer, tenant_id, marker_leave_id)

        snapshot = _snapshot(clickhouse, tenant_id)
        assert snapshot != ([], [], [], [])

        # The rebuild fence: report reads refuse service while the projection
        # is being rebuilt (ADR 0019 decision 7).
        clickhouse.insert(
            "projection_state",
            [["reports", 0, datetime.now(UTC)]],
            column_names=["key", "ready", "updated_at"],
        )
        with pytest.raises(ReportsUnavailableError):
            ClickhouseReportsRepository(clickhouse).ensure_ready()

        for table in SCHEMA_TABLES:
            if table != "projection_state":
                clickhouse.command(f"TRUNCATE TABLE {table}")
        # Raw counts, not the fenced read repository: the rebuild is fenced.
        for table in (
            "dim_employee",
            "dim_assignment",
            "fact_overtime",
            "fact_attendance",
            "fact_leave",
        ):
            assert clickhouse.command(f"SELECT count() FROM {table}") == 0

        # Replay from `earliest` with a fresh group: the fold is a pure
        # function of the stream, so the aggregates reproduce exactly.
        async with _consumer(topic, clickhouse):
            writer = ClickhouseProjectionWriter(clickhouse)
            await _await_marker(writer, tenant_id, marker_leave_id)

        clickhouse.insert(
            "projection_state",
            [["reports", 1, datetime.now(UTC) + timedelta(seconds=1)]],
            column_names=["key", "ready", "updated_at"],
        )
        ClickhouseReportsRepository(clickhouse).ensure_ready()
        assert _snapshot(clickhouse, tenant_id) == snapshot


def _snapshot(
    clickhouse: Client, tenant_id: UUID
) -> tuple[
    list[OvertimeAggregateRow],
    list[AttendanceAggregateRow],
    list[LeaveAggregateRow],
    list[HeadcountAggregateRow],
]:
    repository = ClickhouseReportsRepository(clickhouse)
    return (
        list(repository.overtime_aggregates(_query(tenant_id))),
        list(repository.attendance_aggregates(_query(tenant_id))),
        list(repository.leave_aggregates(_query(tenant_id, group_by=("leave_type",)))),
        list(repository.headcount_aggregates(_query(tenant_id))),
    )


# --- retention -----------------------------------------------------------------


async def test_topic_creation_pins_retention_to_the_report_span() -> None:
    name = f"workpulse-reports-retention-{uuid4().hex}"
    publisher = KafkaEnvelopePublisher(bootstrap_servers=KAFKA_BOOTSTRAP, topic=name)
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP)
    await admin.start()
    try:
        await publisher.start()  # ensures the topic with the pinned retention
        response = await admin.describe_configs(
            [ConfigResource(ConfigResourceType.TOPIC, name)]
        )
        resource = cast(Any, response[0]).resources[0]
        configs = {entry[0]: entry[1] for entry in resource[4]}
        retention_ms = int(configs["retention.ms"])
        # 24 months plus a margin: the reports rebuild replays `earliest`
        # within the topic's retained window (ADR 0019 decision 6).
        assert retention_ms == REPORT_RETENTION_MS
        assert retention_ms >= 25 * 31 * 24 * 60 * 60 * 1000
    finally:
        await publisher.stop()
        try:
            await admin.delete_topics([name])
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        await admin.close()


# --- pre-extension degradation -------------------------------------------------


async def test_pre_extension_envelopes_degrade_honestly(clickhouse: Client) -> None:
    tenant_id = uuid4()
    employee_id = uuid4()
    department_a = uuid4()
    department_b = uuid4()
    position_a = uuid4()
    location_a = uuid4()
    overtime_id = uuid4()
    marker_leave_id = uuid4()

    created = _created(
        tenant_id,
        employee_id,
        department_id=department_a,
        position_id=position_a,
        location_id=location_a,
        hired_at=None,
    )
    # Pre-extension means the additive keys are absent, not null.
    del created["data"]["hired_at"]
    transferred = employee_transferred(
        tenant_id=tenant_id,
        actor_id=uuid4(),
        occurred_at=NOW,
        employee_id=employee_id,
        from_department_id=department_a,
        to_department_id=department_b,
        effective_from=TRANSFER_DATE,
    )
    del transferred["data"]["to_position_id"]
    del transferred["data"]["to_location_id"]
    fact_events = _overtime_events(
        tenant_id,
        employee_id,
        overtime_id,
        work_date=date(2026, 4, 1),
    )

    async with _topic() as topic, _producer() as producer:
        async with _consumer(topic, clickhouse):
            await _publish(
                producer,
                topic,
                created,
                transferred,
                *fact_events,
                _leave(tenant_id, employee_id, marker_leave_id),
            )
            writer = ClickhouseProjectionWriter(clickhouse)
            await _await_marker(writer, tenant_id, marker_leave_id)

    fact = writer.get_overtime(tenant_id, overtime_id)
    assert fact is not None
    assert fact.status == "hr_verified"
    # Missing tenure → the unknown seniority band's anchor; the fact still
    # counts under department/location/position (spec rule 3).
    assert fact.hired_at is None
    assert fact.department_id == department_b
    # Pre-extension transfer: position/location carry forward from the
    # earliest known interval rather than becoming null.
    assert fact.position_id == position_a
    assert fact.location_id == location_a

    dimensions = writer.load_dimensions(tenant_id, employee_id)
    assert dimensions.employee is not None
    assert dimensions.employee.hired_at is None
    assert [row.department_id for row in dimensions.assignments] == [
        department_b,
        department_a,
    ]


# --- malformed / foreign messages ---------------------------------------------


async def test_malformed_and_foreign_messages_do_not_stall_the_worker(
    clickhouse: Client,
) -> None:
    tenant_id = uuid4()
    employee_id = uuid4()
    overtime_id = uuid4()
    marker_leave_id = uuid4()
    unknown = {
        "event_id": "evt_reports_unknown",
        "event_type": f"mystery.{SENTINEL}",
        "version": 1,
        "tenant_id": str(tenant_id),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(uuid4()),
        "data": {},
    }

    async with _topic() as topic, _producer() as producer:
        async with _consumer(topic, clickhouse):
            await _publish(
                producer,
                topic,
                b"this is not json",
                unknown,
                _created(
                    tenant_id,
                    employee_id,
                    department_id=uuid4(),
                    position_id=uuid4(),
                    location_id=uuid4(),
                ),
                *_overtime_events(tenant_id, employee_id, overtime_id),
                _leave(tenant_id, employee_id, marker_leave_id),
            )
            writer = ClickhouseProjectionWriter(clickhouse)
            await _eventually(
                lambda: writer.get_leave(tenant_id, marker_leave_id),
                description="the marker after the skipped messages",
            )

    fact = writer.get_overtime(tenant_id, overtime_id)
    assert fact is not None
    assert fact.status == "hr_verified"
