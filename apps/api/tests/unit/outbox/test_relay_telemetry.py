"""T4 — relay telemetry: one root span per outbox row.

The relay publishes rows in order; each attempt is a worker unit with a
`trace_id`/`span_id`-correlated log line and the bounded
`workpulse_worker_events_total` / `workpulse_worker_unit_duration_seconds`
instruments. A failed row is bookkept and retried by the next poll without
killing the loop (ADR 0023, observability rules 3 and 6).
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import structlog
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.logging import add_trace_context
from tests.unit.telemetry_harness import TelemetryHarness
from workers.relay.relay import OutboxRelay

NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Row:
    id: UUID
    event_id: UUID
    payload: dict[str, Any]


class _FakeSession:
    async def commit(self) -> None:
        pass


class _RelayRepo:
    def __init__(self, rows: Sequence[_Row]) -> None:
        self.rows = list(rows)
        self.published: list[UUID] = []
        self.failed: list[tuple[UUID, str]] = []

    async def list_unpublished(self, limit: int) -> Sequence[_Row]:
        return self.rows[:limit]

    async def mark_published(self, row_id: UUID, at: datetime) -> None:
        self.published.append(row_id)
        self.rows = [row for row in self.rows if row.id != row_id]

    async def mark_failed(self, row_id: UUID, error: str) -> None:
        self.failed.append((row_id, error))


class _RelayPublisher:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.envelopes: list[dict[str, Any]] = []

    async def publish(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)
        if self.error is not None:
            raise self.error


def _relay(repo: _RelayRepo, publisher: _RelayPublisher) -> OutboxRelay:
    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, _FakeSession())

    return OutboxRelay(
        session_factory,
        lambda _session: cast(Any, repo),
        publisher,
        batch_size=10,
        poll_seconds=0.001,
        backoff_base=0.01,
        backoff_max=0.02,
        clock=lambda: NOW,
    )


def _capture_logs() -> Any:
    structlog.configure(cache_logger_on_first_use=False)
    return structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars, add_trace_context]
    )


def _assert_metric(harness: TelemetryHarness, event_type: str, status: str) -> None:
    assert (
        "workpulse_worker_events_total{"
        f'event_type="{event_type}",status="{status}",worker="relay"'
        "} 1.0" in harness.metrics()
    )


async def test_relay_records_succeeded_and_failed_rows(
    telemetry_harness: TelemetryHarness,
) -> None:
    row = _Row(
        id=uuid4(),
        event_id=uuid4(),
        payload={"event_type": "attendance.checked_in", "data": {}},
    )
    repo = _RelayRepo([row])
    publisher = _RelayPublisher(error=RuntimeError("broker down"))
    relay = _relay(repo, publisher)

    with _capture_logs() as logs:
        assert await relay._publish_batch() == (0, True)

    failed_span = telemetry_harness.finished()[0]
    assert failed_span.name == "relay attendance.checked_in"
    assert failed_span.attributes["workpulse.status"] == "failed"
    assert failed_span.status.status_code is StatusCode.ERROR
    assert "broker down" not in str(dict(failed_span.attributes))
    failure_log = next(entry for entry in logs if entry["event"] == "publish_failed")
    assert failure_log["trace_id"] == format(failed_span.context.trace_id, "032x")
    assert failure_log["span_id"] == format(failed_span.context.span_id, "016x")
    assert repo.failed and repo.published == []
    _assert_metric(telemetry_harness, "attendance.checked_in", "failed")

    # The loop survives: the next poll publishes the same row.
    publisher.error = None
    assert await relay._publish_batch() == (1, False)
    assert repo.published == [row.id]

    spans = telemetry_harness.finished()
    assert spans[-1].attributes["workpulse.status"] == "succeeded"
    _assert_metric(telemetry_harness, "attendance.checked_in", "succeeded")
