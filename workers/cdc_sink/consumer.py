"""The CDC projection sink: `workpulse.cdc.*` → ClickHouse (T4, ADR 0024).

One instance (v1). The loop fetches Debezium change envelopes from the six
captured-table topics plus Debezium's heartbeat topic, maps each to a projection
row and commits the Kafka offsets **only after the whole fetched batch has been
written and flushed** (never after the first record of a fetch page — aiokafka
advances the consumed position to the end of the page, so a per-record commit
would acknowledge rows that were not handled yet).

Delete/truncate ops are dropped and counted, never executed: reference data is
soft-deleted (ADR 0007) and the projection never removes a row (feature rule
8). Redelivery is harmless because rows are versioned by the **source commit
timestamp** (`ReplacingMergeTree(updated_at)`), not by ingest time (rule 7).

Readiness (D5): a fresh consumer group fences `projection_state.ready = 0`
before its first fetch; the fence flips to `1` on snapshot-done evidence
(`source.snapshot == "last"`, a heartbeat, or the first non-snapshot change)
after the batch is durable. Evidence older than the current fence is ignored,
so a rebuild cannot be un-fenced by history replayed from before it. A restart
with committed offsets never re-fences a served projection.

The source and writer ports are structural protocols, so unit tests drive this
exact loop with in-memory stubs — no broker, no ClickHouse (`.ai/testing.md`).
Every log line is ids-only: a skipped message is identified by table/op and an
error category, never by its contents (`.ai/security.md`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)
from app.shared.logging import get_logger
from app.shared.telemetry import (
    event_type_label,
    record_cdc_dropped_delete,
    record_cdc_reference_miss,
    record_cdc_sink_error,
    worker_unit,
)
from workers.cdc_sink.mapping import (
    CdcMappingError,
    Change,
    Heartbeat,
    LeaveTypeBook,
    UnknownLeaveTypeError,
    decode_assignment,
    decode_attendance,
    decode_employee,
    decode_leave,
    decode_leave_type,
    decode_overtime,
    parse_message,
)

_log = get_logger("cdc_sink.consumer")

# A bounded exponent: an unbounded `2 ** n` overflows to a float error after
# ~1025 consecutive failures and would kill the loop exactly mid-outage.
_MAX_BACKOFF_EXPONENT = 32

#: How many times a fact waits for its leave-type reference before the sink
#: writes the bounded `unknown` fallback (D7 / R6).
_REFERENCE_RETRIES = 3

Event = Change | Heartbeat


class KafkaMessage(Protocol):
    """One fetched record — `aiokafka`'s `ConsumerRecord` satisfies it."""

    value: bytes | None


class EventSource(Protocol):
    """Bus-consumer port — `AIOKafkaConsumer` satisfies it."""

    async def getmany(
        self, *, timeout_ms: int
    ) -> Mapping[Any, Sequence[KafkaMessage]]: ...

    async def commit(self) -> None: ...


class CdcProjectionWriter(Protocol):
    """The sink's readiness + buffered upsert boundary over one database."""

    def readiness(self) -> tuple[int, datetime] | None: ...

    def mark_rebuilding(self) -> None: ...

    def mark_ready(self) -> None: ...

    def flush(self) -> None: ...

    def upsert_employee(self, row: EmployeeRow, updated_at: datetime) -> None: ...

    def upsert_assignment(self, row: AssignmentRow, updated_at: datetime) -> None: ...

    def upsert_attendance(self, fact: AttendanceFact, updated_at: datetime) -> None: ...

    def upsert_overtime(self, fact: OvertimeFact, updated_at: datetime) -> None: ...

    def upsert_leave(self, fact: LeaveFact, updated_at: datetime) -> None: ...


class CdcSinkConsumer:
    """The fetch → map → buffer → flush → commit loop."""

    def __init__(
        self,
        source: EventSource,
        writer: CdcProjectionWriter,
        *,
        poll_seconds: float,
        backoff_base: float,
        backoff_max: float,
        fresh_group: bool = False,
        leave_types: LeaveTypeBook | None = None,
        reference_retries: int = _REFERENCE_RETRIES,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if backoff_base <= 0 or backoff_max < backoff_base:
            raise ValueError("backoff_max must be >= backoff_base > 0")
        if reference_retries < 0:
            raise ValueError("reference_retries must be >= 0")
        self._source = source
        self._writer = writer
        self._poll_seconds = poll_seconds
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._fresh_group = fresh_group
        self._leave_types = leave_types if leave_types is not None else LeaveTypeBook()
        self._reference_retries = reference_retries
        self._stopping = asyncio.Event()
        self._evidence_seen = False
        # Set once the first fetch round completes: the consumer has joined its
        # group and pinned its `earliest` position — readiness for the gate's
        # log line and for tests, never a sleep (ADR 0013 decision 4).
        self.started = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop gracefully (idempotent, signal-handler safe)."""
        self._stopping.set()

    async def run(self) -> None:
        """Consume until `stop()`; broker hiccups back off, never kill the loop."""
        if self._fresh_group and not await self._fence():
            return

        consecutive_failures = 0
        while not self._stopping.is_set():
            try:
                batches = await self._source.getmany(
                    timeout_ms=max(1, round(self._poll_seconds * 1000))
                )
            except Exception as exc:
                # A broker hiccup must not kill the worker: back off and retry.
                # The type only — never payloads or driver text.
                consecutive_failures += 1
                _log.warning("cdc_sink_fetch_failed", error=type(exc).__name__)
                await self._pause(self._backoff_seconds(consecutive_failures))
                continue
            consecutive_failures = 0
            self.started.set()

            values = [
                message.value for messages in batches.values() for message in messages
            ]
            if not values:
                continue

            # Reference changes in this page must be visible to the facts that
            # follow on a different topic (D7); applying them up front avoids a
            # pointless retry/`unknown` fallback for a reference already here.
            self._warm_batch_references(values)

            evidence_ts: datetime | None = None
            for value in values:
                if self._stopping.is_set():
                    return  # this page stays uncommitted for the next run
                seen = await self._handle(value)
                if seen is not None and (evidence_ts is None or seen > evidence_ts):
                    evidence_ts = seen
            if self._stopping.is_set():
                return

            # Rows are buffered per table and inserted as one block per table;
            # the readiness flip and the offset commit follow the durable write.
            if not await self._flush_with_retry():
                return
            if evidence_ts is not None and not await self._ready_with_retry(
                evidence_ts
            ):
                return
            if not await self._commit():
                _log.warning("cdc_sink_commit_failed")

    async def _fence(self) -> bool:
        """Write `ready = 0` before a fresh group's first fetch, with backoff."""
        failures = 0
        while not self._stopping.is_set():
            try:
                self._writer.mark_rebuilding()
                return True
            except Exception as exc:
                failures += 1
                record_cdc_sink_error()
                _log.warning(
                    "cdc_sink_readiness_failed",
                    error=type(exc).__name__,
                    operation="fence",
                )
                await self._pause(self._backoff_seconds(failures))
        return False

    async def _handle(self, value: bytes | None) -> datetime | None:
        """Process one message; return its snapshot-done timestamp, if any."""
        if not value:
            # Kafka tombstones (null value) are compacted delete markers, not
            # CDC changes; they carry no row and are a deliberate silent skip.
            return None
        try:
            event = parse_message(value)
        except CdcMappingError as exc:
            _log.warning(
                "cdc_sink_message_skipped",
                error="invalid_envelope",
                reason=str(exc),
            )
            return None

        if event is None:
            _log.warning("cdc_sink_message_skipped", error="malformed_envelope")
            return None

        label = (
            "cdc.heartbeat" if isinstance(event, Heartbeat) else f"cdc.{event.table}"
        )
        async with worker_unit("cdc_sink", event_type_label(label)) as unit:
            outcome = await self._process(event)
            if outcome in ("skipped", "stopped"):
                unit.mark_skipped()

        if outcome != "succeeded":
            return None
        if isinstance(event, Heartbeat):
            return event.commit_ts
        return event.commit_ts if event.snapshot_done else None

    async def _process(self, event: Event) -> str:
        """Map one event (buffered); readiness is applied after the flush."""
        if isinstance(event, Heartbeat):
            return "succeeded"
        if event.dropped:
            # A delete/truncate never removes a row (rule 8).
            record_cdc_dropped_delete()
            return "succeeded"
        return await self._apply(event)

    async def _apply(self, change: Change) -> str:
        """Map one change into the writer's buffer, retrying reference misses.

        Store I/O is deferred to `flush()`. A missing leave-type reference
        retries a bounded number of times (the reference could still be behind
        this change on a different topic) and then lands the `unknown`
        fallback, counted (D7). Malformed rows are committed skips.
        """
        reference_failures = 0
        failures = 0
        while not self._stopping.is_set():
            try:
                self._apply_change(change, allow_unknown_type=False)
                return "succeeded"
            except UnknownLeaveTypeError:
                reference_failures += 1
                if reference_failures <= self._reference_retries:
                    _log.warning("cdc_sink_reference_wait", table=change.table)
                    await self._pause(self._backoff_seconds(reference_failures))
                    continue
                try:
                    self._apply_change(change, allow_unknown_type=True)
                except CdcMappingError as exc:
                    # The fallback can still expose another invalid field; it
                    # is a malformed row, not a transitive store failure.
                    _log.warning(
                        "cdc_sink_message_skipped",
                        table=change.table,
                        error="invalid_row",
                        reason=str(exc),
                    )
                    return "skipped"
                record_cdc_reference_miss()
                _log.warning("cdc_sink_reference_miss", table=change.table)
                return "succeeded"
            except CdcMappingError as exc:
                # A captured image the contract rejects: terminal skip. No
                # partial row was written — the mapping validates before I/O.
                _log.warning(
                    "cdc_sink_message_skipped",
                    table=change.table,
                    error="invalid_row",
                    reason=str(exc),
                )
                return "skipped"
            except Exception as exc:
                # A transient writer-buffer failure: retry head-of-line with
                # bounded backoff; the change stays uncommitted.
                failures += 1
                _log.warning(
                    "cdc_sink_projection_failed",
                    table=change.table,
                    error=type(exc).__name__,
                )
                await self._pause(self._backoff_seconds(failures))
                continue
        return "stopped"

    def _apply_change(self, change: Change, *, allow_unknown_type: bool) -> None:
        """Map and buffer one change (one complete row)."""
        after = change.after
        if after is None:  # pragma: no cover - parse_message guards this
            raise CdcMappingError("after is missing")

        table = change.table
        if table == "employees":
            self._writer.upsert_employee(decode_employee(after), change.commit_ts)
        elif table == "employments":
            self._writer.upsert_assignment(decode_assignment(after), change.commit_ts)
        elif table == "attendance_records":
            self._writer.upsert_attendance(decode_attendance(after), change.commit_ts)
        elif table == "overtime_requests":
            self._writer.upsert_overtime(decode_overtime(after), change.commit_ts)
        elif table == "leave_requests":
            fact = decode_leave(
                after, self._leave_types, allow_unknown_type=allow_unknown_type
            )
            if fact is not None:
                self._writer.upsert_leave(fact, change.commit_ts)
        elif table == "leave_types":
            self._leave_types.apply(decode_leave_type(after))
        else:  # pragma: no cover - CAPTURED_TABLES is the closed set
            raise CdcMappingError("table is not captured")

    def _warm_batch_references(self, values: Sequence[bytes | None]) -> None:
        """Apply this page's `leave_types` changes before its leave facts."""
        for value in values:
            try:
                event = parse_message(value)
            except CdcMappingError:
                continue
            if (
                isinstance(event, Change)
                and event.table == "leave_types"
                and not event.dropped
                and event.after is not None
            ):
                try:
                    self._leave_types.apply(decode_leave_type(event.after))
                except CdcMappingError:
                    continue

    async def _flush_with_retry(self) -> bool:
        """Flush the buffered rows, retrying a store outage head-of-line."""
        failures = 0
        while not self._stopping.is_set():
            try:
                self._writer.flush()
                return True
            except Exception as exc:
                failures += 1
                record_cdc_sink_error()
                _log.warning(
                    "cdc_sink_projection_failed",
                    error=type(exc).__name__,
                )
                await self._pause(self._backoff_seconds(failures))
        return False

    async def _ready_with_retry(self, evidence_ts: datetime) -> bool:
        """Flip the readiness fence, retrying a store outage head-of-line."""
        failures = 0
        while not self._stopping.is_set():
            try:
                self._mark_ready_if_needed(evidence_ts)
                return True
            except Exception as exc:
                failures += 1
                record_cdc_sink_error()
                _log.warning(
                    "cdc_sink_readiness_failed",
                    error=type(exc).__name__,
                    operation="ready",
                )
                await self._pause(self._backoff_seconds(failures))
        return False

    def _mark_ready_if_needed(self, evidence_ts: datetime) -> None:
        """Flip the readiness fence once, on current-snapshot evidence (D5).

        A fresh group always flips. A restart reads the current marker:

        - a missing marker is served normally (existing `ensure_ready`
          semantics), so it is left alone but does not latch: a later
          `register.sh` fence is still observed;
        - an already-ready projection (`ready = 1`) latches without a write
          (a restart with committed offsets never touches readiness);
        - an open fence (`ready = 0`) flips only for evidence at or after the
          fence timestamp, so history replayed from before a rebuild cannot
          un-fence a truncated projection.

        A stale-evidence message does not latch, so the next snapshot row or
        heartbeat is still observed.
        """
        if self._evidence_seen:
            return
        if self._fresh_group:
            self._writer.mark_ready()
            self._evidence_seen = True
            return

        state = self._writer.readiness()
        if state is None:
            return
        if state[0] == 1:
            self._evidence_seen = True
            return
        _, fenced_at = state
        if evidence_ts < fenced_at:
            return
        self._writer.mark_ready()
        self._evidence_seen = True

    async def _commit(self) -> bool:
        """Advance offsets past the whole handled page (failures retry)."""
        try:
            await self._source.commit()
            return True
        except Exception as exc:
            _log.warning("cdc_sink_commit_failed", error=type(exc).__name__)
            return False

    def _backoff_seconds(self, consecutive_failures: int) -> float:
        """Exponential between attempts: base, 2×, 4×, … capped at `backoff_max`."""
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


__all__ = ["CdcProjectionWriter", "CdcSinkConsumer", "EventSource", "KafkaMessage"]
