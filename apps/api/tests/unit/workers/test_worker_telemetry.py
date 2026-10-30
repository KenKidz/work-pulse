"""T4 — worker telemetry: per-unit spans, log correlation and bounded metrics.

Every worker seam is driven with in-memory stubs while an isolated telemetry
stack is installed in place of the process-global one: one root span per
batch/message/job, `trace_id`/`span_id` on the unit's log lines, and
`workpulse_worker_events_total` / `workpulse_worker_unit_duration_seconds`
counters for succeeded, failed and skipped units. Failures never kill the
worker loop and never leak the exception message into a span (ADR 0023,
`.ai/features/observability.md` rules 3, 5 and 6).
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace, TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import structlog
from opentelemetry.trace import StatusCode

import app.shared.telemetry as telemetry_module
from app.domains.attendance.events import attendance_checked_in
from app.domains.leave.events import leave_requested
from app.domains.notifications.models import RenderedMessage
from app.domains.notifications.schemas import (
    NotificationIntent,
    Recipient,
    SubjectFacts,
)
from app.shared.logging import add_trace_context, get_logger
from app.shared.telemetry import (
    event_type_label,
    start_worker_metrics_server,
    worker_unit,
)
from tests.unit.telemetry_harness import TelemetryHarness as Harness
from workers.notifications.consumer import NotificationsConsumer, NotificationUnit
from workers.payroll.runner import PAYROLL_RUN_UNIT, PayrollRunWorker
from workers.realtime.consumer import RealtimeConsumer

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
ACTOR = UUID("00000000-0000-4000-8000-000000000031")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
MANAGER = UUID("00000000-0000-4000-8000-000000000003")
MANAGER_USER = UUID("00000000-0000-4000-8000-000000000041")
LEAVE_REQUEST = UUID("00000000-0000-4000-8000-0000000000e1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000a1")
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 29)

MANAGER_RECIPIENT = Recipient(
    employee_id=MANAGER,
    user_id=MANAGER_USER,
    display_name="Mary Manager",
    account_address="mary.manager@example.test",
)


@pytest.fixture
def harness(telemetry_harness: Harness) -> Harness:
    """The shared private telemetry bundle, named for readability here."""
    return telemetry_harness


def _capture_logs() -> Any:
    """Capture structlog events with the production trace-correlation processor."""
    structlog.configure(cache_logger_on_first_use=False)
    return structlog.testing.capture_logs(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_context,
        ]
    )


def _assert_metric(harness: Harness, worker: str, event_type: str, status: str) -> None:
    assert (
        "workpulse_worker_events_total{"
        f'event_type="{event_type}",status="{status}",worker="{worker}"'
        "} 1.0" in harness.metrics()
    )


def _assert_span_correlation(
    span: Any, logs: list[dict[str, Any]], event: str
) -> dict[str, Any]:
    entry = next(item for item in logs if item["event"] == event)
    assert entry["trace_id"] == format(span.context.trace_id, "032x")
    assert entry["span_id"] == format(span.context.span_id, "016x")
    return entry


def _checked_in() -> bytes:
    return json.dumps(
        attendance_checked_in(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            attendance_id=ATTENDANCE,
            employee_id=EMPLOYEE,
            workday=WORKDAY,
            work_mode="onsite",
            late=False,
            minutes_late=None,
        )
    ).encode("utf-8")


def _requested() -> bytes:
    return json.dumps(
        leave_requested(
            tenant_id=TENANT,
            actor_id=ACTOR,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST,
            employee_id=EMPLOYEE,
            leave_type_code="annual",
            start_date=WORKDAY,
            end_date=date(2026, 10, 31),
            days=3,
        )
    ).encode("utf-8")


async def test_worker_unit_records_a_success_span_and_correlates_logs(
    harness: Harness,
) -> None:
    with _capture_logs() as logs:
        async with worker_unit("relay", "attendance.checked_in"):
            get_logger("test").info("inside_unit")

    span = harness.finished()[0]
    assert span.name == "relay attendance.checked_in"
    assert span.attributes["workpulse.worker"] == "relay"
    assert span.attributes["workpulse.event_type"] == "attendance.checked_in"
    assert span.attributes["workpulse.status"] == "succeeded"
    assert span.status.status_code is StatusCode.UNSET
    _assert_span_correlation(span, logs, "inside_unit")
    _assert_metric(harness, "relay", "attendance.checked_in", "succeeded")
    assert (
        "workpulse_worker_unit_duration_seconds_count{"
        'status="succeeded",worker="relay"} 1.0' in harness.metrics()
    )


async def test_worker_unit_records_failure_without_the_exception_message(
    harness: Harness,
) -> None:
    with pytest.raises(RuntimeError):
        async with worker_unit("relay", "attendance.checked_in"):
            raise RuntimeError("broker exploded with secret-value")

    span = harness.finished()[0]
    assert span.attributes["workpulse.status"] == "failed"
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.status.status_code is StatusCode.ERROR
    assert "broker exploded with secret-value" not in str(dict(span.attributes))
    _assert_metric(harness, "relay", "attendance.checked_in", "failed")


async def test_worker_unit_records_a_deliberate_skip(harness: Harness) -> None:
    async with worker_unit("cdc_sink", "employee.created") as unit:
        unit.mark_skipped()

    span = harness.finished()[0]
    assert span.attributes["workpulse.status"] == "skipped"
    assert span.status.status_code is StatusCode.UNSET
    _assert_metric(harness, "cdc_sink", "employee.created", "skipped")


def test_event_type_label_is_bounded_and_pii_safe() -> None:
    assert event_type_label("attendance.checked_in") == "attendance.checked_in"
    assert event_type_label("Ada Lovelace <ada@example.test>") == "unknown"
    assert event_type_label("attendance.checked_in\nsecret") == "unknown"
    assert event_type_label("x" * 100) == "unknown"
    assert event_type_label(None) == "unknown"
    assert event_type_label(42) == "unknown"


def test_metrics_server_port_zero_binds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_start(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))
        raise AssertionError("port 0 is the disabled sentinel")

    monkeypatch.setattr(telemetry_module, "start_http_server", fake_start)

    assert start_worker_metrics_server(0) is None
    assert start_worker_metrics_server(-1) is None
    assert calls == []


# --- realtime ------------------------------------------------------------------


class _RealtimeSource:
    def __init__(self) -> None:
        self.commits = 0

    async def getmany(self, *, timeout_ms: int) -> Mapping[Any, Sequence[Any]]:
        return {}

    async def commit(self) -> None:
        self.commits += 1


class _PresencePublisher:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.facts: list[Any] = []

    async def publish(self, fact: Any) -> None:
        if self.error is not None:
            raise self.error
        self.facts.append(fact)


def _realtime(
    source: _RealtimeSource, publisher: _PresencePublisher
) -> RealtimeConsumer:
    return RealtimeConsumer(
        source,
        publisher,
        poll_seconds=0.001,
        backoff_base=0.01,
        backoff_max=0.02,
        today=lambda: WORKDAY,
    )


async def test_realtime_records_success_and_a_swallowed_publish_failure(
    harness: Harness,
) -> None:
    source = _RealtimeSource()
    publisher = _PresencePublisher()
    consumer = _realtime(source, publisher)

    await consumer._handle(_checked_in())

    span = harness.finished()[0]
    assert span.name == "realtime attendance.checked_in"
    assert span.attributes["workpulse.status"] == "succeeded"
    assert len(publisher.facts) == 1
    assert source.commits == 1
    _assert_metric(harness, "realtime", "attendance.checked_in", "succeeded")

    failing = _PresencePublisher(error=RuntimeError("redis down"))
    failing_consumer = _realtime(source, failing)
    with _capture_logs() as logs:
        await failing_consumer._handle(_checked_in())

    failed_span = harness.finished()[-1]
    assert failed_span.attributes["workpulse.status"] == "failed"
    assert failed_span.attributes["error.type"] == "RuntimeError"
    _assert_span_correlation(failed_span, logs, "consumer_publish_failed")
    # The transient contract: the offset still advances, the loop lives.
    assert source.commits == 2
    _assert_metric(harness, "realtime", "attendance.checked_in", "failed")


async def test_realtime_marks_malformed_messages_as_skipped(harness: Harness) -> None:
    source = _RealtimeSource()
    consumer = _realtime(source, _PresencePublisher())

    await consumer._handle(b"not-json")

    span = harness.finished()[0]
    assert span.attributes["workpulse.event_type"] == "unknown"
    assert span.attributes["workpulse.status"] == "skipped"
    assert source.commits == 1
    _assert_metric(harness, "realtime", "unknown", "skipped")


# --- notifications -------------------------------------------------------------


class _Directory:
    async def subject_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> SubjectFacts | None:
        return SubjectFacts("Ada Lovelace", MANAGER)

    async def recipient(self, tenant_id: UUID, employee_id: UUID) -> Recipient | None:
        return MANAGER_RECIPIENT

    async def actor_display_name(self, tenant_id: UUID, actor_id: UUID) -> str | None:
        return "Grace Hopper"


class _DeliveryService:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    async def handle(
        self,
        intent: NotificationIntent,
        recipient: Recipient,
        message: RenderedMessage,
        now: datetime,
    ) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(terminal=True)


@dataclass(frozen=True, slots=True)
class _UnitContext:
    unit: NotificationUnit

    async def __aenter__(self) -> NotificationUnit:
        return self.unit

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


def _notifications(
    source: _RealtimeSource,
    service: _DeliveryService,
) -> NotificationsConsumer:
    def factory() -> _UnitContext:
        unit = NotificationUnit(
            directory=cast(Any, _Directory()),
            service=cast(Any, service),
        )
        return _UnitContext(unit)

    return NotificationsConsumer(
        source,
        factory,
        poll_seconds=0.001,
        backoff_base=0.01,
        backoff_max=0.02,
        now=lambda: NOW,
    )


async def test_notifications_records_success_and_a_failed_delivery(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _RealtimeSource()
    service = _DeliveryService()
    consumer = _notifications(source, service)

    with _capture_logs():
        await consumer._handle(_requested())

    span = harness.finished()[0]
    assert span.name == "notifications leave.requested"
    assert span.attributes["workpulse.status"] == "succeeded"
    assert service.calls == 1
    assert source.commits == 1
    _assert_metric(harness, "notifications", "leave.requested", "succeeded")

    failing_service = _DeliveryService(error=RuntimeError("store down"))
    failing_consumer = _notifications(source, failing_service)
    pauses: list[float] = []

    async def fake_pause(seconds: float) -> None:
        pauses.append(seconds)
        failing_consumer.stop()

    monkeypatch.setattr(failing_consumer, "_pause", fake_pause)

    with _capture_logs() as logs:
        await failing_consumer._handle(_requested())

    failed_span = harness.finished()[-1]
    assert failed_span.attributes["workpulse.status"] == "failed"
    assert failed_span.attributes["error.type"] == "delivery_failed"
    _assert_span_correlation(failed_span, logs, "consumer_handle_failed")
    assert pauses == [0.01]
    assert source.commits == 1  # no commit past the failed delivery
    _assert_metric(harness, "notifications", "leave.requested", "failed")


async def test_notifications_records_a_deliberate_skip(harness: Harness) -> None:
    source = _RealtimeSource()
    consumer = _notifications(source, _DeliveryService())

    await consumer._handle(_checked_in())

    span = harness.finished()[0]
    assert span.attributes["workpulse.status"] == "skipped"
    assert source.commits == 1
    _assert_metric(harness, "notifications", "attendance.checked_in", "skipped")


# --- payroll -------------------------------------------------------------------


class _PayrollUnit:
    def __init__(self, run_id: UUID, error: BaseException | None = None) -> None:
        self._run_id = run_id
        self._error = error

    async def queued_run_ids(self, limit: int) -> Sequence[UUID]:
        return [self._run_id]

    async def calculate(self, run_id: UUID, now: datetime) -> Any:
        if self._error is not None:
            raise self._error
        return SimpleNamespace(status=SimpleNamespace(value="calculated"))


def _payroll(run_id: UUID, error: BaseException | None = None) -> PayrollRunWorker:
    @asynccontextmanager
    async def factory() -> AsyncIterator[_PayrollUnit]:
        yield _PayrollUnit(run_id, error)

    return PayrollRunWorker(
        factory,
        batch_size=10,
        poll_seconds=0.001,
        backoff_base=0.01,
        backoff_max=0.02,
        clock=lambda: NOW,
    )


async def test_payroll_records_success_and_a_failed_run(harness: Harness) -> None:
    run_id = uuid4()
    worker = _payroll(run_id)

    with _capture_logs() as logs:
        assert await worker.run_once() == 1

    span = harness.finished()[0]
    assert span.name == f"payroll {PAYROLL_RUN_UNIT}"
    assert span.attributes["workpulse.status"] == "succeeded"
    _assert_span_correlation(span, logs, "payroll_run_processed")
    _assert_metric(harness, "payroll", PAYROLL_RUN_UNIT, "succeeded")

    failing = _payroll(uuid4(), error=RuntimeError("database down"))
    with _capture_logs() as failing_logs:
        assert await failing.run_once() == 0

    failed_span = harness.finished()[-1]
    assert failed_span.attributes["workpulse.status"] == "failed"
    assert failed_span.status.status_code is StatusCode.ERROR
    _assert_span_correlation(failed_span, failing_logs, "payroll_run_failed")
    _assert_metric(harness, "payroll", PAYROLL_RUN_UNIT, "failed")
