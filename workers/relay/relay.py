"""Outbox relay: committed rows → Kafka, at-least-once, in insertion order.

One instance (v1 — ADR 0012 decision 11). The loop polls up to `batch_size`
unpublished rows ordered by the UUIDv7 `id` (insertion order ≈ commit order,
so a late check-in's `attendance.checked_in` → `attendance.late` pair keeps
builder order), publishes each stored envelope and marks its row
`published_at` only after the broker acks — each mark in its own committed
transaction, so a crash between ack and mark re-publishes the identical
`event_id` (the documented at-least-once window; harmless to replay-safe
consumers).

A publish failure never drops a row and never fails a user request: the row
records the failure (`attempts` increments, `last_error` gets infrastructure
error text only — payload contents are redacted, `.ai/security.md`) and **the
batch stops**: strict order beats throughput, and the spec accepts the
head-of-line block ("retries, never drops"; no DLQ in v1). The loop then backs
off exponentially between polls. While the broker is down the backlog grows in
PostgreSQL — the write path never depends on the bus — and drains in order
once it returns. Database outages are retried with the same backoff: the
worker is never killed by a downstream hiccup (no supervisor exists in v1).

The repo/publisher/`session_factory` ports are structural protocols, so the
unit tests drive this exact loop with in-memory stubs — no DB, no broker
(`.ai/testing.md`).
"""

import asyncio
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.logging import get_logger
from app.shared.redaction import redaction_needles, safe_error_text
from app.shared.telemetry import event_type_label, worker_unit

_log = get_logger("relay")

# The backoff exponent is bounded: an unbounded `2 ** n` overflows to a float
# error after ~1025 consecutive failures (~9 h at the 30 s cap) and would kill
# the loop mid-outage, exactly when it must keep retrying.
_MAX_BACKOFF_EXPONENT = 32


class OutboxRepository(Protocol):
    """Relay-side repo port — `PostgresOutboxRepository` satisfies it.

    Rows are the real `EventOutboxRow` (the relay is infrastructure; its world
    is ADR 0012's row shape — the relay reads `id`, `event_id` and `payload`).
    """

    async def list_unpublished(self, limit: int) -> Sequence[EventOutboxRow]: ...

    async def mark_published(self, row_id: UUID, at: datetime) -> None: ...

    async def mark_failed(self, row_id: UUID, error: str) -> None: ...


class EnvelopePublisher(Protocol):
    """Broker port — `KafkaEnvelopePublisher` satisfies it."""

    async def publish(self, envelope: dict[str, Any]) -> None: ...


class SessionFactory(Protocol):
    """Callable opening a session context (`async_sessionmaker` satisfies it)."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


def _string_leaves(value: Any) -> Iterator[str]:
    """Every string value in a JSON-shaped payload, nested or not."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_leaves(item)


def _safe_error(exc: BaseException, envelope: dict[str, Any]) -> str:
    """`Type: message` diagnostics for `last_error` — payload-free by contract.

    The shared scrubber redacts every payload string value and its JSON-escaped
    form before whitespace normalization and caps the result. Payload contents
    — and therefore PII — never reach the error column (`.ai/security.md`).
    """
    return safe_error_text(exc, redaction_needles(_string_leaves(envelope)))


def _event_type(envelope: Mapping[str, Any]) -> str:
    """Bounded span/metric label from the stored envelope (never raw text)."""
    return event_type_label(envelope.get("event_type"))


class OutboxRelay:
    """The poll → publish → mark loop (at-least-once, insertion order)."""

    def __init__(
        self,
        session_factory: SessionFactory,
        repo: Callable[[AsyncSession], OutboxRepository],
        publisher: EnvelopePublisher,
        *,
        batch_size: int,
        poll_seconds: float,
        backoff_base: float,
        backoff_max: float,
        clock: Callable[[], datetime],
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if backoff_base <= 0 or backoff_max < backoff_base:
            raise ValueError("backoff_max must be >= backoff_base > 0")
        self._session_factory = session_factory
        self._repo = repo
        self._publisher = publisher
        self._batch_size = batch_size
        self._poll_seconds = poll_seconds
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._clock = clock
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop gracefully (idempotent, signal-handler safe).

        Checked before each poll and between rows: an in-flight row always
        finishes (published or failure-recorded) first; the next row stays
        unpublished for the next run.
        """
        self._stopping.set()

    async def run(self) -> None:
        """Drain unpublished rows, polling in order until `stop()`.

        Broker failures are bookkept per row; database failures back the loop
        off and retry — the worker never dies with the backlog undrained.
        """
        consecutive_failures = 0
        while not self._stopping.is_set():
            try:
                published, failed = await self._publish_batch()
            except SQLAlchemyError as exc:
                # A database outage must not kill the worker (no supervisor
                # exists in v1): back off and retry — the API keeps committing
                # and rows are never lost. The type only: driver messages can
                # embed statement text.
                consecutive_failures += 1
                _log.warning("database_unavailable", error=type(exc).__name__)
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            if self._stopping.is_set():
                break
            if failed:
                consecutive_failures += 1
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            consecutive_failures = 0
            if published < self._batch_size:
                # A short batch means the backlog is drained: wait for new rows
                # instead of busy-polling.
                await self._pause(self._poll_seconds)

    async def _publish_batch(self) -> tuple[int, bool]:
        """Publish rows in `id` order; return `(published_count, failed)`.

        `failed` means a row's publish failed and was recorded: the batch
        stopped at it so the remaining rows are not overtaken (strict order).
        """
        rows = await self._list_unpublished()
        published = 0
        for row in rows:
            if self._stopping.is_set():
                break  # stop between rows: this row is not published this run
            async with worker_unit("relay", _event_type(row.payload)) as unit:
                try:
                    await self._publisher.publish(row.payload)
                except Exception as exc:
                    # Any publish failure is bookkept and retried by a later
                    # poll — never propagated (a broker outage must not kill
                    # the worker). Recorded and logged inside the unit span so
                    # the failure line stays trace-correlated.
                    unit.mark_failed(type(exc).__name__)
                    await self._mark_failed(row.id, _safe_error(exc, row.payload))
                    _log.warning(
                        "publish_failed",
                        event_id=row.event_id,
                        error=type(exc).__name__,
                    )
                    return published, True
            await self._mark_published(row.id)
            published += 1
        return published, False

    def _backoff_seconds(self, consecutive_failures: int) -> float:
        """Exponential between polls: base, 2×, 4×, … capped at `backoff_max`."""
        exponent = min(consecutive_failures - 1, _MAX_BACKOFF_EXPONENT)
        return min(self._backoff_base * 2**exponent, self._backoff_max)

    async def _pause(self, seconds: float) -> None:
        """Sleep, but wake immediately on `stop()`."""
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _list_unpublished(self) -> Sequence[EventOutboxRow]:
        async with self._session_factory() as session:
            return await self._repo(session).list_unpublished(self._batch_size)

    async def _mark_published(self, row_id: UUID) -> None:
        async with self._session_factory() as session:
            await self._repo(session).mark_published(row_id, self._clock())
            await session.commit()

    async def _mark_failed(self, row_id: UUID, error: str) -> None:
        async with self._session_factory() as session:
            await self._repo(session).mark_failed(row_id, error)
            await session.commit()
