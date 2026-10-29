"""Shared unit-test fixtures."""

from collections.abc import Iterator

import pytest

import app.shared.telemetry as telemetry_module
from tests.unit.telemetry_harness import TelemetryHarness, create_harness


@pytest.fixture
def telemetry_harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[TelemetryHarness]:
    """Install a private telemetry bundle for the duration of one test."""
    harness = create_harness()
    monkeypatch.setattr(telemetry_module, "_TELEMETRY", harness.telemetry)
    try:
        yield harness
    finally:
        harness.telemetry.tracer_provider.shutdown()
        harness.telemetry.meter_provider.shutdown()
