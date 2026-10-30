"""Contract tests — CDC change envelopes vs. the v1 JSON schemas.

Guards the Phase-5 CDC invariants before the connector or sink exists:

1. Every synthetic captured change validates against
   `contracts/cdc/change-envelope.v1.json` and the matching per-table row
   schema under `contracts/cdc/rows/`.
2. The captured column sets are exactly the plan's D8 sets, row schemas are
   strict (`additionalProperties: false`), and no PII-shaped column can enter
   the stream — the contract-level PII gate (`.ai/security.md`).
3. Debezium wire encodings are pinned (epoch-day dates, decimal-as-string
   `hours`) and connector metadata extras are allowed.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACTS_DIR = REPO_ROOT / "contracts" / "cdc"
ENVELOPE_PATH = CONTRACTS_DIR / "change-envelope.v1.json"
ROWS_DIR = CONTRACTS_DIR / "rows"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "cdc"
RECORDED_DIR = FIXTURES_DIR / "recorded"

# Rule 4 / D8 — the captured tables and their exact column include sets.
# The connector config gate (T3) asserts the same sets; contract and pipeline
# cannot drift without one of the gates failing.
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

OP_BY_OP_CLASS: dict[str, str] = {
    "created": "c",
    "updated": "u",
    "deleted": "d",
    "snapshot": "r",
    "truncated": "t",
}

# PII-shaped key markers. Row images must never contain one; Debezium's
# connector metadata (`source.name`) is not a row image and is out of scope.
_PII_KEY_MARKERS = (
    "name",
    "email",
    "phone",
    "salary",
    "reason",
    "note",
    "decision",
    "document",
    "address",
    "birth",
    "national",
    "iban",
    "bank",
)

_MISSING = object()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _pii_keys(obj: Any) -> list[str]:
    """Return keys in a JSON tree whose names look PII-shaped."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if any(marker in key.lower() for marker in _PII_KEY_MARKERS):
                found.append(key)
            found.extend(_pii_keys(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_pii_keys(item))
    return found


def _fixture_cases() -> list[Any]:
    cases: list[Any] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        table, op_class = path.stem.rsplit(".", 1)
        cases.append(pytest.param(path, table, op_class, id=f"{table}-{op_class}"))
    return cases


FIXTURE_CASES = _fixture_cases()


def _snapshot_row(table: str) -> dict[str, Any]:
    """The full `after` image used as a base for tamper tests."""
    return _load(FIXTURES_DIR / f"{table}.snapshot.json")["after"]


def _mutate(
    envelope: dict[str, Any],
    path: tuple[str, ...],
    value: Any = _MISSING,
) -> dict[str, Any]:
    mutated = deepcopy(envelope)
    target: dict[str, Any] = mutated
    for key in path[:-1]:
        target = target[key]
    if value is _MISSING:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return mutated


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared CDC change envelope every captured message validates against."""
    schema = _load(ENVELOPE_PATH)
    Draft202012Validator.check_schema(schema)
    return schema


@pytest.fixture(scope="module")
def envelope_validator(
    envelope_schema: dict[str, Any],
) -> Draft202012Validator:
    return Draft202012Validator(envelope_schema)


@pytest.fixture(scope="module")
def row_schemas() -> dict[str, dict[str, Any]]:
    """One checked Draft 2020-12 row schema per captured table."""
    schemas: dict[str, dict[str, Any]] = {}
    for schema_path in sorted(ROWS_DIR.glob("*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        schemas[schema_path.name.removesuffix(".v1.json")] = schema
    return schemas


@pytest.fixture(scope="module")
def row_validators(
    row_schemas: dict[str, dict[str, Any]],
) -> dict[str, Draft202012Validator]:
    return {
        table: Draft202012Validator(schema) for table, schema in row_schemas.items()
    }


def test_row_schemas_cover_exactly_the_captured_tables(
    row_schemas: dict[str, dict[str, Any]],
) -> None:
    """One row contract per captured table — no more, no less (rule 4)."""
    assert set(row_schemas) == set(CAPTURED_TABLES)


def test_envelope_table_enum_matches_the_captured_tables(
    envelope_schema: dict[str, Any],
) -> None:
    """`source.table` is the captured-table allowlist, in schema form."""
    source = envelope_schema["$defs"]["source"]
    assert set(source["properties"]["table"]["enum"]) == set(CAPTURED_TABLES)


def test_contract_ids_follow_the_workpulse_convention(
    envelope_schema: dict[str, Any],
    row_schemas: dict[str, dict[str, Any]],
) -> None:
    """Every schema has the stable `$id` consumers resolve against."""
    assert (
        envelope_schema["$id"]
        == "https://workpulse.dev/contracts/cdc/change-envelope.v1.json"
    )
    for table, schema in row_schemas.items():
        assert (
            schema["$id"] == f"https://workpulse.dev/contracts/cdc/rows/{table}.v1.json"
        )


@pytest.mark.parametrize(("table", "columns"), sorted(CAPTURED_COLUMNS.items()))
def test_row_schemas_pin_exactly_the_captured_columns(
    row_schemas: dict[str, dict[str, Any]],
    table: str,
    columns: set[str],
) -> None:
    """Row property sets are exactly D8 and every captured column is required."""
    schema = row_schemas[table]
    assert set(schema["properties"]) == columns
    assert set(schema["required"]) == columns
    assert schema["additionalProperties"] is False


def _recorded_cases() -> list[Any]:
    cases: list[Any] = []
    for path in sorted(RECORDED_DIR.glob("*.json")):
        table, op_class = path.stem.rsplit(".", 1)
        cases.append(
            pytest.param(path, table, op_class, id=f"recorded-{table}-{op_class}")
        )
    return cases


RECORDED_CASES = _recorded_cases()


def test_fixtures_cover_every_table_and_op_class() -> None:
    """Synthetic fixtures exist for every captured table × operation class."""
    expected = {
        f"{table}.{op_class}"
        for table in CAPTURED_TABLES
        for op_class in OP_BY_OP_CLASS
    }
    assert {path.stem for path in FIXTURES_DIR.glob("*.json")} == expected


@pytest.mark.parametrize(("fixture_path", "table", "op_class"), FIXTURE_CASES)
def test_fixture_validates_envelope_and_row_contract(
    envelope_validator: Draft202012Validator,
    row_validators: dict[str, Draft202012Validator],
    fixture_path: Path,
    table: str,
    op_class: str,
) -> None:
    """Every captured change validates as an envelope plus its row image."""
    envelope = _load(fixture_path)

    envelope_validator.validate(envelope)

    assert envelope["op"] == OP_BY_OP_CLASS[op_class]
    assert envelope["source"]["table"] == table
    assert envelope["source"]["snapshot"] in (
        {"true", "last"} if envelope["op"] == "r" else {"false"}
    )

    after = envelope["after"]
    if after is not None:
        row_validators[table].validate(after)
        assert set(after) == CAPTURED_COLUMNS[table]

    before = envelope["before"]
    if before is not None:
        # Synthetic fixtures carry full row images; real Debezium may filter
        # `before` to replica-identity columns (see the partial-before test).
        # Delete fixtures have no `after`, so this is the only row validation
        # that exercises their row contract.
        row_validators[table].validate(before)
        assert set(before) == CAPTURED_COLUMNS[table]


def test_recorded_fixtures_cover_every_table_and_op_class() -> None:
    """Real Debezium 3.2.4 messages are checked in for the whole matrix (T7)."""
    expected = {
        f"{table}.{op_class}"
        for table in CAPTURED_TABLES
        for op_class in OP_BY_OP_CLASS
    }
    assert {path.stem for path in RECORDED_DIR.glob("*.json")} == expected


@pytest.mark.parametrize(("fixture_path", "table", "op_class"), RECORDED_CASES)
def test_recorded_fixture_validates_the_contract(
    envelope_validator: Draft202012Validator,
    row_validators: dict[str, Draft202012Validator],
    fixture_path: Path,
    table: str,
    op_class: str,
) -> None:
    """Every recorded message validates the envelope; post-images validate rows.

    Debezium's `before` image is intentionally not row-schema validated for
    updates/deletes: with `column.include.list` it can be null (updates) or a
    placeholder-filled key image (deletes). The sink never uses it — updates
    upsert the post-image and deletes are dropped — so only `after` is a
    projection input.
    """
    envelope = _load(fixture_path)

    envelope_validator.validate(envelope)

    assert envelope["op"] == OP_BY_OP_CLASS[op_class]
    assert envelope["source"]["table"] == table

    after = envelope["after"]
    if after is not None:
        row_validators[table].validate(after)
        assert set(after) == CAPTURED_COLUMNS[table]

    pii = _pii_keys({"before": envelope["before"], "after": after})
    assert pii == [], f"PII-shaped key(s) in {fixture_path.name}: {pii}"


def test_recorded_messages_pin_real_debezium_wire_behavior() -> None:
    """The recorded set deliberately includes the behaviours that bit us:

    - a streaming update with a null `before` (column filtering removes the
      replica-identity image);
    - Debezium 3.x snapshot markers beyond `true`/`last`;
    - processing timestamps in µs/ns.
    """
    snapshots = [_load(path) for path in RECORDED_DIR.glob("*.snapshot.json")]
    assert {envelope["source"]["snapshot"] for envelope in snapshots} >= {
        "first",
        "first_in_data_collection",
        "last_in_data_collection",
    }

    updates = [_load(path) for path in RECORDED_DIR.glob("*.updated.json")]
    assert any(envelope["before"] is None for envelope in updates)

    for envelope in snapshots + updates:
        assert "ts_us" in envelope
        assert "ts_ns" in envelope


def test_snapshot_fixtures_cover_true_and_last_markers() -> None:
    """Both snapshot markers the readiness fence depends on are represented."""
    markers = {
        _load(path)["source"]["snapshot"]
        for path in FIXTURES_DIR.glob("*.snapshot.json")
    }
    assert markers == {"true", "last"}


@pytest.mark.parametrize(("fixture_path", "table", "op_class"), FIXTURE_CASES)
def test_fixture_rows_carry_no_pii_keys(
    fixture_path: Path,
    table: str,
    op_class: str,
) -> None:
    """No PII-shaped key appears in any captured row image (rule 3)."""
    envelope = _load(fixture_path)

    pii = _pii_keys({"before": envelope["before"], "after": envelope["after"]})
    assert pii == [], f"PII-shaped key(s) in {fixture_path.name}: {pii}"


def test_row_schema_property_names_carry_no_pii_keys(
    row_schemas: dict[str, dict[str, Any]],
) -> None:
    """The declared column set itself is PII-free, not just its fixtures."""
    for table, schema in row_schemas.items():
        pii = _pii_keys(schema["properties"])
        assert pii == [], f"PII-shaped column(s) in {table}: {pii}"


@pytest.mark.parametrize(
    ("table", "pii_column"),
    [
        pytest.param("employees", "email", id="employees-email"),
        pytest.param("employments", "employee_name", id="employments-name"),
        pytest.param("attendance_records", "note", id="attendance-note"),
        pytest.param("overtime_requests", "hr_note", id="overtime-note"),
        pytest.param("leave_requests", "reason", id="leave-reason"),
        pytest.param("leave_types", "name", id="leave-types-name"),
    ],
)
def test_row_schemas_reject_an_extra_pii_column(
    row_validators: dict[str, Draft202012Validator],
    table: str,
    pii_column: str,
) -> None:
    """`additionalProperties: false` is the contract-level PII gate."""
    tampered = {**_snapshot_row(table), pii_column: "sensitive"}
    with pytest.raises(ValidationError):
        row_validators[table].validate(tampered)


@pytest.mark.parametrize(("table", "columns"), sorted(CAPTURED_COLUMNS.items()))
def test_row_schemas_reject_a_missing_captured_column(
    row_validators: dict[str, Draft202012Validator],
    table: str,
    columns: set[str],
) -> None:
    """Every captured column is required — a filtered row image is not a row."""
    missing = sorted(columns)[0]
    tampered = _snapshot_row(table)
    del tampered[missing]
    with pytest.raises(ValidationError):
        row_validators[table].validate(tampered)


@pytest.mark.parametrize(
    ("table", "tampering"),
    [
        pytest.param(
            "employees",
            {"hired_at": "2025-01-15"},
            id="employees-epoch-day-as-iso-string",
        ),
        pytest.param(
            "employments",
            {"effective_from": "2025-01-15"},
            id="employments-epoch-day-as-iso-string",
        ),
        pytest.param(
            "attendance_records",
            {"late": "true"},
            id="attendance-boolean-as-string",
        ),
        pytest.param(
            "attendance_records",
            {"duration_minutes": 480.5},
            id="attendance-integer-as-float",
        ),
        pytest.param(
            "overtime_requests",
            {"hours": 2.5},
            id="overtime-decimal-as-number",
        ),
        pytest.param(
            "overtime_requests",
            {"status": "approved"},
            id="overtime-unknown-status",
        ),
        pytest.param(
            "leave_requests",
            {"days": 0},
            id="leave-non-positive-days",
        ),
        pytest.param(
            "leave_requests",
            {"status": "granted"},
            id="leave-unknown-status",
        ),
        pytest.param(
            "leave_types",
            {"code": 123},
            id="leave-types-code-as-number",
        ),
    ],
)
def test_row_schemas_reject_wrong_wire_encodings(
    row_validators: dict[str, Draft202012Validator],
    table: str,
    tampering: dict[str, Any],
) -> None:
    """D9 wire encodings and pinned state values are enforced."""
    tampered = {**_snapshot_row(table), **tampering}
    with pytest.raises(ValidationError):
        row_validators[table].validate(tampered)


def test_envelope_allows_debezium_source_extras(
    envelope_validator: Draft202012Validator,
) -> None:
    """Connector metadata may grow; only the pinned source fields are required."""
    envelope = _load(FIXTURES_DIR / "employees.created.json")
    envelope["source"]["slot_name"] = "workpulse_slot"
    envelope_validator.validate(envelope)


def test_envelope_allows_debezium_3x_timestamps_and_snapshot_markers(
    envelope_validator: Draft202012Validator,
) -> None:
    """Debezium 3.x adds µs/ns timestamps and richer snapshot markers."""
    envelope = _load(FIXTURES_DIR / "employees.snapshot.json")
    envelope["ts_us"] = 1767225600001000
    envelope["ts_ns"] = 1767225600001000000

    for marker in (
        "first",
        "first_in_data_collection",
        "last_in_data_collection",
        "last",
    ):
        envelope["source"]["snapshot"] = marker
        envelope_validator.validate(envelope)

    with pytest.raises(ValidationError):
        envelope["source"]["snapshot"] = "maybe"
        envelope_validator.validate(envelope)


def test_envelope_allows_optional_transaction_metadata(
    envelope_validator: Draft202012Validator,
) -> None:
    """Transaction metadata is optional and never part of the pipeline contract."""
    envelope = _load(FIXTURES_DIR / "employees.created.json")

    envelope["transaction"] = {
        "id": "0:1234567:552",
        "total_order": 1,
        "data_collection_order": 1,
    }
    envelope_validator.validate(envelope)

    del envelope["transaction"]
    envelope_validator.validate(envelope)


def test_envelope_allows_a_filtered_before_image(
    envelope_validator: Draft202012Validator,
) -> None:
    """Real updates/deletes may carry only replica-identity columns in `before`."""
    envelope = _load(FIXTURES_DIR / "employees.updated.json")
    envelope["before"] = {"id": "00000000-0000-4000-8000-000000000002"}
    envelope_validator.validate(envelope)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("op",), "x", id="unknown-op"),
        pytest.param(
            ("source", "table"),
            "payroll_runs",
            id="uncaptured-table",
        ),
        pytest.param(
            ("source", "snapshot"),
            "maybe",
            id="unknown-snapshot-marker",
        ),
        pytest.param(("source", "lsn"), "24023168", id="lsn-as-string"),
        pytest.param(
            ("source", "ts_ms"),
            "1767225600000",
            id="commit-ts-as-string",
        ),
        pytest.param(("after",), None, id="create-without-row"),
        pytest.param(
            ("before",),
            {"id": "00000000-0000-4000-8000-000000000002"},
            id="create-with-before",
        ),
    ],
)
def test_envelope_rejects_tampered_changes(
    envelope_validator: Draft202012Validator,
    path: tuple[str, ...],
    value: Any,
) -> None:
    """Op semantics, the captured-table allowlist and source encodings are strict."""
    envelope = _mutate(_load(FIXTURES_DIR / "employees.created.json"), path, value)
    with pytest.raises(ValidationError):
        envelope_validator.validate(envelope)


def test_envelope_rejects_a_delete_with_an_after_image(
    envelope_validator: Draft202012Validator,
) -> None:
    """Delete ops carry `before` only — the sink must never see an `after`."""
    envelope = _load(FIXTURES_DIR / "employees.deleted.json")
    envelope["after"] = envelope["before"]
    with pytest.raises(ValidationError):
        envelope_validator.validate(envelope)


def test_envelope_requires_every_pinned_source_field(
    envelope_validator: Draft202012Validator,
) -> None:
    """Missing `source.lsn` is a contract failure, not an ingest surprise."""
    envelope = _mutate(
        _load(FIXTURES_DIR / "employees.created.json"),
        ("source", "lsn"),
    )
    with pytest.raises(ValidationError):
        envelope_validator.validate(envelope)


def test_envelope_rejects_unknown_top_level_fields(
    envelope_validator: Draft202012Validator,
) -> None:
    """The wrapper is strict; only Debezium's documented fields are allowed."""
    envelope = _load(FIXTURES_DIR / "employees.created.json")
    envelope["headers"] = {}
    with pytest.raises(ValidationError):
        envelope_validator.validate(envelope)


def test_truncate_ops_carry_no_row_images(
    envelope_validator: Draft202012Validator,
) -> None:
    """Truncate changes are counted by the sink, never replayed as deletes."""
    envelope = _load(FIXTURES_DIR / "employees.truncated.json")
    assert envelope["before"] is None
    assert envelope["after"] is None
    envelope_validator.validate(envelope)
