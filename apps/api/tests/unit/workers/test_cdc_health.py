"""T8 unit tests — CDC pipeline health probes and gauges (ADR 0024).

The Connect status mapping, the slot/consumer lag probes and the poll loop are
pure/testable seams: no broker, PostgreSQL or Connect instance is required.
Metric labels stay bounded (`connector`, `slot`, `topic`, closed state set) and
a failing probe never escapes the poll loop (ADR 0023 business rule 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from aiokafka import TopicPartition

from tests.unit.telemetry_harness import TelemetryHarness
from workers.cdc_sink.health import (
    CONNECTOR_STATES,
    CdcHealthPoller,
    connector_state_from_status,
    fetch_consumer_lag,
)

CONNECTOR = "workpulse-cdc"
SLOT = "workpulse_cdc_test"
TOPICS = ("workpulse.cdc.employees", "workpulse.cdc.attendance_records")


def test_connector_state_collapses_connector_and_task_states() -> None:
    assert (
        connector_state_from_status(
            {"connector": {"state": "RUNNING"}, "tasks": [{"state": "RUNNING"}]}
        )
        == "RUNNING"
    )
    # A failed task wins even when the connector object still says RUNNING.
    assert (
        connector_state_from_status(
            {"connector": {"state": "RUNNING"}, "tasks": [{"state": "FAILED"}]}
        )
        == "FAILED"
    )
    assert (
        connector_state_from_status(
            {"connector": {"state": "PAUSED"}, "tasks": [{"state": "PAUSED"}]}
        )
        == "PAUSED"
    )
    # Missing/garbled payloads collapse to the bounded UNKNOWN label.
    assert connector_state_from_status({}) == "UNKNOWN"
    assert connector_state_from_status({"connector": {"state": "exploded"}}) == (
        "UNKNOWN"
    )


class _LagSource:
    def __init__(
        self,
        *,
        assignment: set[TopicPartition],
        ends: Mapping[TopicPartition, int],
        committed: Mapping[TopicPartition, int | None],
    ) -> None:
        self._assignment = assignment
        self._ends = ends
        self._committed = committed

    def assignment(self) -> set[TopicPartition]:
        return self._assignment

    async def end_offsets(
        self, partitions: Sequence[TopicPartition]
    ) -> Mapping[TopicPartition, int]:
        return {partition: self._ends[partition] for partition in partitions}

    async def committed(self, partition: TopicPartition) -> int | None:
        return self._committed[partition]


async def test_consumer_lag_sums_uncommitted_offsets_per_topic() -> None:
    employee_partition = TopicPartition(TOPICS[0], 0)
    attendance_partition = TopicPartition(TOPICS[1], 0)
    source = _LagSource(
        assignment={employee_partition, attendance_partition},
        ends={employee_partition: 10, attendance_partition: 4},
        committed={employee_partition: 7, attendance_partition: None},
    )

    assert await fetch_consumer_lag(source, TOPICS) == {
        TOPICS[0]: 3,
        TOPICS[1]: 4,
    }


async def test_consumer_lag_falls_back_to_first_partition_before_assignment() -> None:
    employee_partition = TopicPartition(TOPICS[0], 0)
    attendance_partition = TopicPartition(TOPICS[1], 0)
    source = _LagSource(
        assignment=set(),
        ends={employee_partition: 2, attendance_partition: 3},
        committed={employee_partition: 1, attendance_partition: 0},
    )

    assert await fetch_consumer_lag(source, TOPICS) == {
        TOPICS[0]: 1,
        TOPICS[1]: 3,
    }


async def test_poll_publishes_bounded_one_hot_state_and_lag_gauges(
    telemetry_harness: TelemetryHarness,
) -> None:
    async def state() -> str:
        return "RUNNING"

    async def slot_lag() -> int:
        return 1234

    async def consumer_lag() -> Mapping[str, int]:
        return {TOPICS[0]: 5, TOPICS[1]: 0}

    poller = CdcHealthPoller(
        connector_name=CONNECTOR,
        slot_name=SLOT,
        topics=TOPICS,
        fetch_connector_state=state,
        fetch_slot_lag=slot_lag,
        fetch_consumer_lag=consumer_lag,
    )

    await poller.poll_once()
    text = telemetry_harness.metrics()

    assert (
        f'workpulse_cdc_connector_state{{connector="{CONNECTOR}",'
        'state="RUNNING"} 1.0' in text
    )
    assert (
        f'workpulse_cdc_connector_state{{connector="{CONNECTOR}",'
        'state="FAILED"} 0.0' in text
    )
    assert f'workpulse_cdc_slot_lag_bytes{{slot="{SLOT}"}} 1234.0' in text
    assert f'workpulse_cdc_consumer_lag{{topic="{TOPICS[0]}"}} 5.0' in text
    for state_name in CONNECTOR_STATES:
        assert f'state="{state_name}"' in text


async def test_poll_keeps_running_when_every_probe_fails(
    telemetry_harness: TelemetryHarness,
) -> None:
    async def boom() -> Any:
        raise RuntimeError("probe down")

    poller = CdcHealthPoller(
        connector_name=CONNECTOR,
        slot_name=SLOT,
        topics=TOPICS,
        fetch_connector_state=boom,
        fetch_slot_lag=boom,
        fetch_consumer_lag=boom,
    )

    await poller.poll_once()
    text = telemetry_harness.metrics()

    # The connector gauge still reports the bounded UNKNOWN state; the other
    # probes simply have no fresh sample (the worker loop stays alive).
    assert (
        f'workpulse_cdc_connector_state{{connector="{CONNECTOR}",'
        'state="UNKNOWN"} 1.0' in text
    )
    assert "workpulse_cdc_slot_lag_bytes" not in text
    assert "workpulse_cdc_consumer_lag" not in text
