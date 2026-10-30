"""CDC pipeline stack-as-code tests (feature T3, ADR 0024).

The Debezium connector, the compose wiring and the Makefile lifecycle targets
live in version control. These tests keep the captured-table list equal to the
feature's rule-4 set, the include lists equal to the row contracts under
`contracts/cdc/rows/`, and PII columns out of Kafka entirely (rule 3). The
mutation-style cases prove the gate fails *naming* the offending table or
column — the observability config-gate precedent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CONNECTOR_PATH = REPO_ROOT / "ops" / "cdc" / "connector.json"
REGISTER_SCRIPT = REPO_ROOT / "ops" / "cdc" / "register.sh"
ROW_CONTRACTS = REPO_ROOT / "contracts" / "cdc" / "rows"
COMPOSE = REPO_ROOT / "docker-compose.yml"
MAKEFILE = REPO_ROOT / "Makefile"

# Feature rule 4 / D8 — the exact captured-table and column sets. The contract
# suite (`tests/contract/test_cdc_envelope.py`) pins the same sets; connector,
# row schemas and contract fixtures cannot drift without a gate failing.
CAPTURED_TABLES: tuple[str, ...] = (
    "employees",
    "employments",
    "attendance_records",
    "overtime_requests",
    "leave_requests",
    "leave_types",
)

CAPTURED_COLUMNS: dict[str, set[str]] = {
    "employees": {"id", "tenant_id", "hired_at", "terminated_at"},
    "employments": {
        "tenant_id",
        "employee_id",
        "department_id",
        "position_id",
        "location_id",
        "effective_from",
        "effective_to",
    },
    "attendance_records": {
        "id",
        "tenant_id",
        "employee_id",
        "workday",
        "duration_minutes",
        "late",
        "minutes_late",
    },
    "overtime_requests": {
        "id",
        "tenant_id",
        "employee_id",
        "work_date",
        "hours",
        "status",
    },
    "leave_requests": {
        "id",
        "tenant_id",
        "employee_id",
        "leave_type_id",
        "start_date",
        "days",
        "status",
    },
    "leave_types": {"id", "tenant_id", "code"},
}

#: Column names that would carry PII into Kafka/ClickHouse. Matched as whole
#: underscore-separated segments so `check_in_note` and `employee_name` hit
#: while `leave_type_code` and `minutes_late` do not (rule 3 — no carve-out).
_PII_SEGMENTS = {
    "name",
    "names",
    "email",
    "phone",
    "salary",
    "salaries",
    "reason",
    "reasons",
    "note",
    "notes",
    "decision",
    "decided",
    "address",
    "ssn",
    "birth",
    "national",
    "iban",
    "bank",
    "document",
    "filename",
}
_PII_COLUMN = re.compile(r"^(" + "|".join(sorted(_PII_SEGMENTS)) + r")$", re.IGNORECASE)


def _load_connector() -> dict[str, Any]:
    data = json.loads(CONNECTOR_PATH.read_text())
    assert isinstance(data, dict)
    return data


def _row_contract_columns() -> dict[str, set[str]]:
    contracts: dict[str, set[str]] = {}
    for path in sorted(ROW_CONTRACTS.glob("*.v1.json")):
        schema = json.loads(path.read_text())
        contracts[path.name.removesuffix(".v1.json")] = set(schema["properties"])
    return contracts


def _captured_tables(config: dict[str, Any]) -> set[str]:
    entries = [part.strip() for part in config["table.include.list"].split(",")]
    assert all(entry.startswith("public.") for entry in entries), entries
    return {entry.removeprefix("public.") for entry in entries}


def _captured_columns(config: dict[str, Any]) -> dict[str, set[str]]:
    columns: dict[str, set[str]] = {}
    for fqn in config["column.include.list"].split(","):
        parts = fqn.strip().split(".")
        assert len(parts) == 3 and parts[0] == "public", fqn
        columns.setdefault(parts[1], set()).add(parts[2])
    return columns


def _pii_columns(table: str, columns: set[str]) -> list[str]:
    return [
        f"{table}.{column}"
        for column in sorted(columns)
        if any(_PII_COLUMN.search(segment) for segment in column.split("_"))
    ]


def _assert_captured_scope(config: dict[str, Any]) -> None:
    """The connector's table and column scope is exactly rule 4 / D8.

    Raises `AssertionError` naming the extra/missing table or column, and
    `... naming the PII column` when an include-list addition is PII-shaped.
    """
    tables = _captured_tables(config)
    if tables != set(CAPTURED_TABLES):
        missing = sorted(set(CAPTURED_TABLES) - tables)
        extra = sorted(tables - set(CAPTURED_TABLES))
        raise AssertionError(
            f"captured table set drifted (missing={missing}, extra={extra})"
        )

    columns = _captured_columns(config)
    if set(columns) != set(CAPTURED_TABLES):
        raise AssertionError(
            f"include lists cover {sorted(columns)}, expected {sorted(CAPTURED_TABLES)}"
        )

    for table in CAPTURED_TABLES:
        leaked = _pii_columns(table, columns[table])
        if leaked:
            raise AssertionError(f"PII column captured: {', '.join(leaked)}")

    for table in CAPTURED_TABLES:
        missing = sorted(CAPTURED_COLUMNS[table] - columns[table])
        extra = sorted(columns[table] - CAPTURED_COLUMNS[table])
        if missing or extra:
            raise AssertionError(
                f"{table}: include-list drift (missing={missing}, extra={extra})"
            )


def _assert_contracts_match(
    config: dict[str, Any],
    contracts: dict[str, set[str]] | None = None,
) -> None:
    contracts = contracts if contracts is not None else _row_contract_columns()
    if set(contracts) != set(CAPTURED_TABLES):
        raise AssertionError(
            f"row contracts cover {sorted(contracts)}, "
            f"expected {sorted(CAPTURED_TABLES)}"
        )
    columns = _captured_columns(config)
    for table in CAPTURED_TABLES:
        if columns[table] != contracts[table]:
            raise AssertionError(
                f"{table}: connector/contract drift "
                f"(connector={sorted(columns[table])}, "
                f"contract={sorted(contracts[table])})"
            )


def _mutated(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    config = deepcopy(_load_connector()["config"])
    mutate(config)
    return config


def _append_include(config: dict[str, Any], *fqns: str) -> None:
    config["column.include.list"] += "," + ",".join(fqns)


def test_connector_captures_exactly_the_rule_4_tables() -> None:
    _assert_captured_scope(_load_connector()["config"])


def test_connector_include_lists_match_the_row_contracts() -> None:
    config = _load_connector()["config"]
    _assert_contracts_match(config)
    # The row contracts are the strict shape the sink parses.
    for path in sorted(ROW_CONTRACTS.glob("*.v1.json")):
        schema = json.loads(path.read_text())
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_connector_routes_topics_to_the_pinned_names() -> None:
    """Debezium's default `<prefix>.<schema>.<table>` is rewired to the spec's
    `workpulse.cdc.<table>` topic per captured table."""
    config = _load_connector()["config"]

    assert config["topic.prefix"] == "workpulse.cdc"
    assert config["transforms"] == "route"
    assert (
        config["transforms.route.type"]
        == "org.apache.kafka.connect.transforms.RegexRouter"
    )
    assert config["transforms.route.regex"] == r"workpulse\.cdc\.public\.(.*)"
    assert config["transforms.route.replacement"] == "workpulse.cdc.$1"


def test_connector_pins_the_wire_and_snapshot_contract() -> None:
    config = _load_connector()["config"]

    assert config["connector.class"] == (
        "io.debezium.connector.postgresql.PostgresConnector"
    )
    assert config["plugin.name"] == "pgoutput"
    assert config["snapshot.mode"] == "initial"
    assert config["publication.autocreate.mode"] == "filtered"
    assert config["decimal.handling.mode"] == "string"
    assert int(config["heartbeat.interval.ms"]) > 0
    assert config["key.converter.schemas.enable"] == "false"
    assert config["value.converter.schemas.enable"] == "false"


def test_gate_rejects_an_extra_table() -> None:
    config = _mutated(
        lambda c: c.__setitem__(
            "table.include.list", c["table.include.list"] + ",public.payroll_runs"
        )
    )
    with pytest.raises(AssertionError, match="payroll_runs"):
        _assert_captured_scope(config)


def test_gate_rejects_a_missing_table() -> None:
    config = _mutated(
        lambda c: c.__setitem__(
            "table.include.list",
            "public.employees,public.employments,public.attendance_records,"
            "public.overtime_requests,public.leave_requests",
        )
    )
    with pytest.raises(AssertionError, match="leave_types"):
        _assert_captured_scope(config)


def test_gate_rejects_an_extra_non_pii_column() -> None:
    config = _mutated(lambda c: _append_include(c, "public.employees.employee_code"))
    with pytest.raises(AssertionError, match="employee_code"):
        _assert_captured_scope(config)


@pytest.mark.parametrize(
    ("table", "column"),
    [
        pytest.param("employees", "email", id="employees-email"),
        pytest.param("leave_requests", "reason", id="leave-reason"),
        pytest.param("overtime_requests", "hr_note", id="overtime-hr-note"),
        pytest.param("attendance_records", "check_in_note", id="attendance-note"),
        pytest.param("employments", "employee_name", id="employments-name"),
    ],
)
def test_gate_rejects_a_pii_column_naming_it(table: str, column: str) -> None:
    config = _mutated(lambda c: _append_include(c, f"public.{table}.{column}"))
    with pytest.raises(AssertionError, match=column):
        _assert_captured_scope(config)


def test_gate_rejects_contract_and_connector_drift() -> None:
    """A contract that gains a column the connector does not capture fails."""
    drifted = _row_contract_columns()
    drifted["employees"].add("email")
    with pytest.raises(AssertionError, match="email"):
        _assert_contracts_match(_load_connector()["config"], drifted)


def test_compose_wires_internal_kafka_listeners_without_moving_host_ports() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]

    cases = {
        "kafka": (
            "127.0.0.1:9092:9092",
            "PLAINTEXT://:9092,INTERNAL://:29092,CONTROLLER://:9093",
            "PLAINTEXT://localhost:9092,INTERNAL://kafka:29092",
        ),
        "kafka-test": (
            "127.0.0.1:9093:9093",
            "PLAINTEXT://:9093,INTERNAL://:29093,CONTROLLER://:19093",
            "PLAINTEXT://localhost:9093,INTERNAL://kafka-test:29093",
        ),
    }
    for name, (host_port, listeners, advertised) in cases.items():
        env = services[name]["environment"]
        assert services[name]["ports"] == [host_port], name
        assert env["KAFKA_LISTENERS"] == listeners, name
        assert env["KAFKA_ADVERTISED_LISTENERS"] == advertised, name
        assert env["KAFKA_INTER_BROKER_LISTENER_NAME"] == "INTERNAL", name
        assert "INTERNAL:PLAINTEXT" in env["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"]


def test_compose_wires_the_connect_pair() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]

    cases = {
        "kafka-connect": ("127.0.0.1:8083:8083", "kafka:29092", "kafka"),
        "kafka-connect-test": (
            "127.0.0.1:8084:8083",
            "kafka-test:29093",
            "kafka-test",
        ),
    }
    for name, (host_port, bootstrap, dependency) in cases.items():
        service = services[name]
        assert service["image"].startswith("quay.io/debezium/connect:")
        assert service["ports"] == [host_port], name
        assert service["environment"]["BOOTSTRAP_SERVERS"] == bootstrap, name
        assert service["depends_on"][dependency]["condition"] == "service_healthy", name
        assert "healthcheck" in service, name

    assert (
        services["kafka-connect"]["environment"]["GROUP_ID"]
        != (services["kafka-connect-test"]["environment"]["GROUP_ID"])
    )


def test_compose_enables_logical_wal_on_both_databases() -> None:
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    for name in ("postgres", "postgres-test"):
        assert services[name]["command"] == [
            "postgres",
            "-c",
            "wal_level=logical",
            "-c",
            "max_slot_wal_keep_size=1GB",
        ], name


def test_makefile_exposes_the_cdc_lifecycle_targets() -> None:
    makefile = MAKEFILE.read_text()
    targets = ("cdc-up", "cdc-down", "cdc-test-up", "cdc-test-down")

    for target in targets:
        assert re.search(rf"^{target}:", makefile, re.MULTILINE), target

    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
    for target in targets:
        assert target in phony, target

    # `cdc-up` runs the parallel pipeline against the D2 dev shadow, never the
    # fold-served canonical database; the test target uses the test shadow.
    assert "CDC_PROJECTION_DATABASE=workpulse_cdc" in makefile
    assert "CDC_PROJECTION_DATABASE=workpulse_test_cdc" in makefile


def test_register_script_seeds_readiness_before_creating_the_connector() -> None:
    script = REGISTER_SCRIPT.read_text()
    seed = "VALUES ('reports', 0, now64(6))"
    create_check = 'if [[ "$status" == "200" ]]'

    # The executable INSERT (not a comment) lives in the create branch, after
    # the existing-connector check and before the POST that creates it.
    assert seed in script
    assert "database/clickhouse/schema.sql" in script
    assert "json.load" in script
    assert script.index(seed) > script.index(create_check)
    assert script.index(seed) < script.index("curl -fsS -X POST")
