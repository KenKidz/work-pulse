"""An isolated telemetry stack for worker unit tests (T4, ADR 0023).

Worker seams read the process-global bundle through `get_telemetry`; tests
replace it with this harness so spans, metrics and log correlation land in a
private registry instead of whichever stack the rest of the process uses.
"""

from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.config import Settings
from app.shared.telemetry import Telemetry, create_telemetry


@dataclass
class TelemetryHarness:
    telemetry: Telemetry
    spans: InMemorySpanExporter

    def metrics(self) -> str:
        """Prometheus text exposition for this harness's registry."""
        return self.telemetry.metrics_text().decode()

    def finished(self) -> list[Any]:
        """Finished spans, flushing the batch processor first."""
        self.telemetry.tracer_provider.force_flush()
        return list(self.spans.get_finished_spans())


def create_harness() -> TelemetryHarness:
    exporter = InMemorySpanExporter()
    stack = create_telemetry(
        Settings(otel_exporter_otlp_endpoint=None),
        span_exporter=exporter,
    )
    return TelemetryHarness(telemetry=stack, spans=exporter)
