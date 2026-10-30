"""Observability stack-as-code tests (feature T5, ADR 0023).

Prometheus scrape targets, Grafana provisioning and the overview dashboard are
checked into `ops/observability/`. These tests keep them in step with the
runtime `Settings` ports, the compose lifecycle and the bounded metric-label
policy (business rule 5).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[4]
OBSERVABILITY_DIR = REPO_ROOT / "ops" / "observability"
PROMETHEUS_CONFIG = OBSERVABILITY_DIR / "prometheus" / "prometheus.yml"
GRAFANA_DATASOURCE = (
    OBSERVABILITY_DIR / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
)
GRAFANA_DASHBOARDS = (
    OBSERVABILITY_DIR / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
)
DASHBOARD = OBSERVABILITY_DIR / "grafana" / "dashboards" / "workpulse-overview.json"
COMPOSE = REPO_ROOT / "docker-compose.yml"
MAKEFILE = REPO_ROOT / "Makefile"

API_METRICS_PORT = 8000

#: Dev ports for the per-worker Prometheus listeners (spec decision 1). The
#: matching `Settings` defaults are asserted below so config and runtime cannot
#: drift; tests override them to `0` (see `tests/conftest.py`).
WORKER_METRICS_PORTS = {
    "relay": 9101,
    "realtime": 9102,
    "notifications": 9103,
    "payroll": 9104,
    "reports": 9105,
    "cdc_sink": 9106,
}

#: Metrics the `## API contract` table exports. Dashboard queries may only
#: reference these (histogram suffixes are stripped before the check).
BOUNDED_METRICS = {
    "workpulse_http_requests_total",
    "workpulse_http_request_duration_seconds",
    "workpulse_http_requests_in_flight",
    "workpulse_worker_events_total",
    "workpulse_worker_unit_duration_seconds",
    "workpulse_cdc_connector_state",
    "workpulse_cdc_slot_lag_bytes",
    "workpulse_cdc_consumer_lag",
    "workpulse_cdc_dropped_deletes_total",
    "workpulse_cdc_sink_errors_total",
}

FORBIDDEN_LABELS = {
    "tenant_id",
    "employee_id",
    "user_id",
    "request_id",
    "event_id",
}

#: Panel title → metric the panel must query.
REQUIRED_PANELS = {
    "HTTP p95 latency": "workpulse_http_request_duration_seconds",
    "HTTP error rate": "workpulse_http_requests_total",
    "HTTP request throughput": "workpulse_http_requests_total",
    "In-flight requests": "workpulse_http_requests_in_flight",
    "Worker throughput": "workpulse_worker_events_total",
    "Worker failures": "workpulse_worker_events_total",
    "CDC connector state": "workpulse_cdc_connector_state",
    "CDC replication slot lag": "workpulse_cdc_slot_lag_bytes",
    "CDC consumer lag": "workpulse_cdc_consumer_lag",
    "CDC dropped deletes": "workpulse_cdc_dropped_deletes_total",
    "CDC sink errors": "workpulse_cdc_sink_errors_total",
}

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# Minimal structural contract for the provisioned dashboard: Grafana itself
# validates the full schema on boot, but the gate must catch a broken or empty
# file before `make observability-up`.
DASHBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["uid", "title", "panels"],
    "properties": {
        "uid": {"const": "workpulse-overview"},
        "title": {"type": "string", "minLength": 1},
        "panels": {
            "type": "array",
            "minItems": 4,
            "items": {
                "type": "object",
                "required": ["id", "type", "title", "targets"],
                "properties": {
                    "id": {"type": "integer"},
                    "type": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "targets": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["expr"],
                            "properties": {
                                "expr": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
    },
}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def _dashboard() -> dict[str, Any]:
    data = json.loads(DASHBOARD.read_text())
    assert isinstance(data, dict)
    return data


def _port(target: str) -> int:
    host, _, port = target.rpartition(":")
    assert host, target
    return int(port)


def _scrape_targets() -> dict[str, list[str]]:
    config = _load_yaml(PROMETHEUS_CONFIG)
    targets: dict[str, list[str]] = {}
    for job in config["scrape_configs"]:
        assert job.get("metrics_path", "/metrics") == "/metrics"
        targets[job["job_name"]] = [
            target
            for static_config in job.get("static_configs", [])
            for target in static_config.get("targets", [])
        ]
    return targets


def test_prometheus_scrapes_the_api_and_every_worker_port() -> None:
    targets = _scrape_targets()

    job_ports = {
        job: {_port(target) for target in job_targets}
        for job, job_targets in targets.items()
    }
    assert job_ports == {
        "workpulse-api": {API_METRICS_PORT},
        "workpulse-workers": set(WORKER_METRICS_PORTS.values()),
    }
    assert {
        target.rsplit(":", 1)[0]
        for job_targets in targets.values()
        for target in job_targets
    } == {"host.docker.internal"}

    for worker, port in WORKER_METRICS_PORTS.items():
        default = Settings.model_fields[f"{worker}_metrics_port"].default
        assert default == port, f"{worker} Settings default drifted from prometheus.yml"


def test_grafana_provisions_prometheus_and_the_dashboard_folder() -> None:
    datasources = _load_yaml(GRAFANA_DATASOURCE)["datasources"]
    prometheus = next(item for item in datasources if item["type"] == "prometheus")

    assert prometheus["uid"] == "workpulse-prometheus"
    assert prometheus["url"] == "http://prometheus:9090"
    assert prometheus["isDefault"] is True

    providers = _load_yaml(GRAFANA_DASHBOARDS)["providers"]
    provider = next(item for item in providers if item["type"] == "file")

    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"
    assert provider["allowUiUpdates"] is False
    assert DASHBOARD.is_file()


def test_dashboard_is_valid_and_defines_the_required_panels() -> None:
    dashboard = _dashboard()
    jsonschema.validate(dashboard, DASHBOARD_SCHEMA)

    panels = {panel["title"]: panel for panel in dashboard["panels"]}
    assert set(REQUIRED_PANELS) <= set(panels)

    for title, metric in REQUIRED_PANELS.items():
        expressions = [target["expr"] for target in panels[title]["targets"]]
        assert any(metric in expr for expr in expressions), title


def test_dashboard_queries_only_bounded_metrics_and_labels() -> None:
    expressions = [
        target["expr"]
        for panel in _dashboard()["panels"]
        for target in panel["targets"]
    ]
    assert expressions

    referenced = set(re.findall(r"\bworkpulse_[a-z0-9_]+\b", " ".join(expressions)))
    for metric in referenced:
        base = metric
        for suffix in ("_bucket", "_count", "_sum"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        assert base in BOUNDED_METRICS, metric

    for expr in expressions:
        for label in FORBIDDEN_LABELS:
            assert not re.search(rf"\b{label}\b", expr), (label, expr)
        # No concrete identifier may be hard-coded into a query string.
        assert not _UUID_PATTERN.search(expr), expr


def test_compose_exposes_the_stack_on_host_loopback() -> None:
    services = _load_yaml(COMPOSE)["services"]

    assert services["prometheus"]["ports"] == ["127.0.0.1:9090:9090"]
    assert services["grafana"]["ports"] == ["127.0.0.1:3001:3000"]
    assert "host.docker.internal:host-gateway" in services["prometheus"]["extra_hosts"]

    prometheus_volumes = "\n".join(services["prometheus"]["volumes"])
    assert "ops/observability/prometheus/prometheus.yml" in prometheus_volumes

    grafana_volumes = "\n".join(services["grafana"]["volumes"])
    assert "ops/observability/grafana/provisioning" in grafana_volumes
    assert "ops/observability/grafana/dashboards" in grafana_volumes

    grafana_env = services["grafana"]["environment"]
    assert grafana_env["GF_AUTH_ANONYMOUS_ENABLED"] == "true"
    assert grafana_env["GF_AUTH_ANONYMOUS_ORG_ROLE"] == "Viewer"


def test_makefile_exposes_the_observability_lifecycle_targets() -> None:
    makefile = MAKEFILE.read_text()

    for target in ("observability-up", "observability-down"):
        assert re.search(rf"^{target}:", makefile, re.MULTILINE), target

    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
    assert "observability-up" in phony
    assert "observability-down" in phony
