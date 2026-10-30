"""CDC pipeline health probes for the sink's Prometheus target (T8, ADR 0024).

The sink exposes operational gauges next to its throughput counters:

- `workpulse_cdc_connector_state` — the Debezium connector/task state read from
  the Kafka Connect REST API (one-hot over a closed state vocabulary);
- `workpulse_cdc_slot_lag_bytes` — WAL retained by the PostgreSQL replication
  slot (`pg_replication_slots`), the source-side backlog;
- `workpulse_cdc_consumer_lag` — uncommitted offset lag per captured topic, the
  sink-side backlog.

Probes are independent and best-effort: a Connect REST/PG/Kafka hiccup is
logged by category and never stops the sink (a metrics scrape is optional, the
projection is not — ADR 0023 business rule 6). Labels are bounded identifiers
(`connector`, `slot`, `topic`) and a closed state set; tenant/employee ids are
never labels (feature rule 12 / ADR 0023 business rule 5).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from aiokafka import TopicPartition
from sqlalchemy import text

from app.infrastructure.postgres.engine import get_session_factory
from app.shared.logging import get_logger
from app.shared.telemetry import (
    record_cdc_connector_state,
    record_cdc_consumer_lag,
    record_cdc_slot_lag,
)

_log = get_logger("cdc_sink.health")

#: The connector/task states Kafka Connect reports. Anything else — including
#: an unreachable REST API — collapses to `UNKNOWN` so the label set is closed.
CONNECTOR_STATES: tuple[str, ...] = (
    "UNASSIGNED",
    "RUNNING",
    "PAUSED",
    "FAILED",
    "RESTARTING",
    "STOPPED",
    "UNKNOWN",
)

#: Precedence when the connector and its tasks disagree: the most actionable
#: failure state wins, then transitional states.
_STATE_PRECEDENCE: tuple[str, ...] = (
    "FAILED",
    "RESTARTING",
    "PAUSED",
    "STOPPED",
    "UNASSIGNED",
    "RUNNING",
)

DEFAULT_POLL_SECONDS = 15.0
_CONNECT_TIMEOUT_SECONDS = 5.0


def connector_state_from_status(payload: Mapping[str, Any]) -> str:
    """Collapse a Connect `/status` payload into one bounded state label.

    A failed task makes the whole connector `FAILED` (the connector object can
    still report `RUNNING` after a task crash). Transitional/stopped states are
    surfaced when no failure is present; a well-formed all-running connector is
    `RUNNING`.
    """
    connector = payload.get("connector")
    connector_state = connector.get("state") if isinstance(connector, Mapping) else None
    task_states: list[Any] = []
    tasks = payload.get("tasks")
    if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes)):
        for task in tasks:
            if isinstance(task, Mapping):
                task_states.append(task.get("state"))

    observed = [
        state
        for state in (connector_state, *task_states)
        if isinstance(state, str) and state in CONNECTOR_STATES
    ]
    if not observed:
        return "UNKNOWN"
    for state in _STATE_PRECEDENCE:
        if state in observed:
            return state
    return "UNKNOWN"


def _fetch_connector_status(
    connect_url: str, connector_name: str, timeout: float
) -> Mapping[str, Any] | None:
    """Read one connector status synchronously (called in a worker thread)."""
    url = f"{connect_url.rstrip('/')}/connectors/{connector_name}/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return payload if isinstance(payload, Mapping) else None


async def fetch_connector_state(
    connect_url: str,
    connector_name: str,
    *,
    timeout: float = _CONNECT_TIMEOUT_SECONDS,
) -> str:
    """The current connector state, or `UNKNOWN` when Connect cannot answer."""
    payload = await asyncio.to_thread(
        _fetch_connector_status, connect_url, connector_name, timeout
    )
    if payload is None:
        return "UNKNOWN"
    return connector_state_from_status(payload)


async def fetch_slot_lag_bytes(slot_name: str) -> int | None:
    """WAL bytes retained at `slot_name`, or `None` when the slot is absent.

    `restart_lsn` is the oldest WAL the slot still requires; the difference to
    the current LSN is the source backlog a stopped/failed pipeline is
    accumulating. A missing slot is not an error (it can be re-created by the
    connector's snapshot procedure).
    """
    async with get_session_factory()() as session:
        result = await session.execute(
            text(
                "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) "
                "FROM pg_replication_slots WHERE slot_name = :slot_name"
            ),
            {"slot_name": slot_name},
        )
        row = result.first()
    if row is None or row[0] is None:
        return None
    return max(0, int(row[0]))


class _LagConsumer(Protocol):
    """The `AIOKafkaConsumer` admin slice the lag probe needs."""

    def assignment(self) -> set[TopicPartition]: ...

    async def end_offsets(
        self, partitions: Sequence[TopicPartition]
    ) -> Mapping[TopicPartition, int]: ...

    async def committed(self, partition: TopicPartition) -> int | None: ...


async def fetch_consumer_lag(
    source: _LagConsumer, topics: Sequence[str]
) -> dict[str, int]:
    """Sum uncommitted offset lag per captured topic.

    The group's assignment is used when it has joined; before the first fetch
    the probe falls back to each topic's first partition (the pipeline creates
    one partition per table). A group with no committed offset yet is measured
    from zero, which is the true backlog on a first start.
    """
    partitions = [
        partition for partition in source.assignment() if partition.topic in topics
    ]
    if not partitions:
        partitions = [TopicPartition(topic, 0) for topic in topics]

    end_offsets = await source.end_offsets(partitions)
    lag: dict[str, int] = {}
    for partition in partitions:
        end = end_offsets.get(partition)
        if end is None:
            continue
        committed = await source.committed(partition)
        base = committed if committed is not None and committed >= 0 else 0
        lag[partition.topic] = lag.get(partition.topic, 0) + max(0, end - base)
    return lag


class CdcHealthPoller:
    """Periodic, best-effort probe loop behind the sink's metrics listener."""

    def __init__(
        self,
        *,
        connector_name: str,
        slot_name: str,
        topics: Sequence[str],
        fetch_connector_state: Callable[[], Awaitable[str]],
        fetch_slot_lag: Callable[[], Awaitable[int | None]],
        fetch_consumer_lag: Callable[[], Awaitable[Mapping[str, int]]],
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        self._connector_name = connector_name
        self._slot_name = slot_name
        self._topics = tuple(topics)
        self._fetch_connector_state = fetch_connector_state
        self._fetch_slot_lag = fetch_slot_lag
        self._fetch_consumer_lag = fetch_consumer_lag
        self._poll_seconds = poll_seconds
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        """Ask the loop to stop (idempotent, signal-handler safe)."""
        self._stopping.set()

    async def run(self) -> None:
        """Poll until stopped; probe failures never escape the loop."""
        while not self._stopping.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                continue

    async def poll_once(self) -> None:
        """Run every probe once and publish the bounded gauges."""
        state = "UNKNOWN"
        try:
            state = await self._fetch_connector_state()
        except Exception as exc:  # noqa: BLE001 - metrics are best-effort
            _log.warning(
                "cdc_health_probe_failed",
                probe="connector",
                error=type(exc).__name__,
            )
            state = "UNKNOWN"
        if state not in CONNECTOR_STATES:
            state = "UNKNOWN"
        record_cdc_connector_state(
            self._connector_name,
            {
                candidate: 1 if candidate == state else 0
                for candidate in CONNECTOR_STATES
            },
        )

        try:
            lag_bytes = await self._fetch_slot_lag()
        except Exception as exc:  # noqa: BLE001 - metrics are best-effort
            _log.warning(
                "cdc_health_probe_failed",
                probe="slot",
                error=type(exc).__name__,
            )
        else:
            if lag_bytes is not None:
                record_cdc_slot_lag(self._slot_name, lag_bytes)

        try:
            consumer_lag = await self._fetch_consumer_lag()
        except Exception as exc:  # noqa: BLE001 - metrics are best-effort
            _log.warning(
                "cdc_health_probe_failed",
                probe="consumer",
                error=type(exc).__name__,
            )
        else:
            for topic, lag in consumer_lag.items():
                if topic in self._topics:
                    record_cdc_consumer_lag(topic, max(0, int(lag)))


__all__ = [
    "CONNECTOR_STATES",
    "DEFAULT_POLL_SECONDS",
    "CdcHealthPoller",
    "connector_state_from_status",
    "fetch_connector_state",
    "fetch_consumer_lag",
    "fetch_slot_lag_bytes",
]
