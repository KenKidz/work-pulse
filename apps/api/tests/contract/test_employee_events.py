"""Contract tests — employee event builders vs. the v1 JSON schemas.

Guards the two Phase-1 invariants that Phase 3 depends on:

1. The pure builders in `app.domains.employees.events` produce payloads that
   validate against `contracts/events/employees/*.v1.json` and wrap in an
   envelope that validates against
   `contracts/events/common/event-envelope.v1.json`.
2. No PII-shaped key (name/email/phone/salary/reason) can appear in any
   payload — the "no PII in events" invariant, proven before any producer or
   consumer exists (rule 4).
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.employees import events

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "employees"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000003")
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-000000000004")
POSITION_ID = UUID("00000000-0000-4000-8000-000000000005")
LOCATION_ID = UUID("00000000-0000-4000-8000-000000000006")
NEW_DEPARTMENT_ID = UUID("00000000-0000-4000-8000-000000000007")
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
EFFECTIVE = date(2026, 9, 28)
HIRED_AT = date(2025, 1, 15)

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per employee event schema."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("employee-*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "employee-created.v1.json",
        lambda: events.employee_created(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            employee_id=EMPLOYEE_ID,
            department_id=DEPARTMENT_ID,
            position_id=POSITION_ID,
            location_id=LOCATION_ID,
            employment_type="full_time",
            hired_at=HIRED_AT,
        ),
    ),
    (
        "employee-updated.v1.json",
        lambda: events.employee_updated(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            employee_id=EMPLOYEE_ID,
            changed_fields=["first_name", "email"],
        ),
    ),
    (
        "employee-transferred.v1.json",
        lambda: events.employee_transferred(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            employee_id=EMPLOYEE_ID,
            from_department_id=DEPARTMENT_ID,
            to_department_id=NEW_DEPARTMENT_ID,
            effective_from=EFFECTIVE,
            to_position_id=POSITION_ID,
            to_location_id=LOCATION_ID,
        ),
    ),
    (
        "employee-terminated.v1.json",
        lambda: events.employee_terminated(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            employee_id=EMPLOYEE_ID,
            effective_date=EFFECTIVE,
        ),
    ),
]


BUILDERS_BY_SCHEMA = dict(BUILDER_CASES)


def _pii_keys(obj: Any) -> list[str]:
    """Return keys in the payload tree whose names look PII-shaped."""
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


def test_schema_files_match_builder_cases(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Every `contracts/events/employees/*.v1.json` schema has a builder case."""
    assert set(validators) == {schema_file for schema_file, _ in BUILDER_CASES}


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_builder_output_validates_contract(
    validators: dict[str, Draft202012Validator],
    envelope_schema: dict[str, Any],
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """The whole event validates against the envelope and its own schema."""
    event = build()

    envelope_validator = Draft202012Validator(envelope_schema)
    envelope_validator.validate(event)

    validators[schema_file].validate(event["data"])


def test_additive_extension_fields_are_filled(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Producers fill the analytics fields (ADR 0019) and payloads validate."""
    created = BUILDERS_BY_SCHEMA["employee-created.v1.json"]()
    assert created["data"]["hired_at"] == HIRED_AT.isoformat()
    validators["employee-created.v1.json"].validate(created["data"])

    transferred = BUILDERS_BY_SCHEMA["employee-transferred.v1.json"]()
    assert transferred["data"]["to_position_id"] == str(POSITION_ID)
    assert transferred["data"]["to_location_id"] == str(LOCATION_ID)
    validators["employee-transferred.v1.json"].validate(transferred["data"])


def test_pre_extension_payloads_still_validate(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Envelopes stored before the extension still validate (ADR 0019).

    The new fields are optional via `required`-list membership, so replaying
    pre-extension outbox rows or topic messages is valid, not an error.
    """
    created = {
        key: value
        for key, value in BUILDERS_BY_SCHEMA["employee-created.v1.json"]()[
            "data"
        ].items()
        if key != "hired_at"
    }
    validators["employee-created.v1.json"].validate(created)

    transferred = {
        key: value
        for key, value in BUILDERS_BY_SCHEMA["employee-transferred.v1.json"]()[
            "data"
        ].items()
        if key not in ("to_position_id", "to_location_id")
    }
    validators["employee-transferred.v1.json"].validate(transferred)


def test_extension_fields_are_nullable(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Builders always fill the keys; absent producer data serializes as null."""
    created = events.employee_created(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        department_id=DEPARTMENT_ID,
        position_id=POSITION_ID,
        location_id=LOCATION_ID,
        employment_type="full_time",
    )
    assert created["data"]["hired_at"] is None
    validators["employee-created.v1.json"].validate(created["data"])

    transferred = events.employee_transferred(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        from_department_id=DEPARTMENT_ID,
        to_department_id=NEW_DEPARTMENT_ID,
        effective_from=EFFECTIVE,
    )
    assert transferred["data"]["to_position_id"] is None
    assert transferred["data"]["to_location_id"] is None
    validators["employee-transferred.v1.json"].validate(transferred["data"])


@pytest.mark.parametrize(
    ("schema_file", "tampering"),
    [
        pytest.param(
            "employee-created.v1.json",
            {"hired_at": 20260115},
            id="hired-at-integer",
        ),
        pytest.param(
            "employee-created.v1.json",
            {"hired_at": "15/01/2025"},
            id="hired-at-bad-format",
        ),
        pytest.param(
            "employee-transferred.v1.json",
            {"to_position_id": 123},
            id="to-position-id-integer",
        ),
        pytest.param(
            "employee-transferred.v1.json",
            {"to_location_id": 123},
            id="to-location-id-integer",
        ),
    ],
)
def test_extension_fields_reject_wrong_types(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    tampering: dict[str, Any],
) -> None:
    tampered = {**BUILDERS_BY_SCHEMA[schema_file]()["data"], **tampering}
    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_builder_output_carries_no_pii(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """No PII-shaped key can appear anywhere in an event payload."""
    event = build()

    pii = _pii_keys(event)
    assert pii == [], f"PII-shaped key(s) in {schema_file}: {pii}"

    allowed_keys = {
        "event_id",
        "event_type",
        "version",
        "tenant_id",
        "occurred_at",
        "actor_id",
        "data",
    }
    for key, value in event.items():
        assert key in allowed_keys
        assert value is not None


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_schema_rejects_pii_keys(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """`additionalProperties: false` rejects any key outside the whitelist."""
    tampered = {**build()["data"], "first_name": "Jane"}
    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


def test_builder_event_type_matches_schema_file() -> None:
    """`event_type` = `<domain>.<event>` mirrors the schema filename."""
    for schema_file, build in BUILDER_CASES:
        event = build()
        stem = schema_file.removesuffix(".json").replace("-", ".")
        domain, event_name, version = stem.split(".")
        assert event["event_type"] == f"{domain}.{event_name}"
        assert event["version"] == int(version.removeprefix("v"))


def test_changed_fields_never_hints_pii_values() -> None:
    """`employee.updated` names changed fields only — values live in audit."""
    event = events.employee_updated(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        changed_fields=["first_name", "last_name", "email", "phone"],
    )
    assert set(event["data"]) == {"employee_id", "changed_fields"}
    assert all(isinstance(field, str) for field in event["data"]["changed_fields"])
    flat = json.dumps(event)
    assert "Jane" not in flat and "@" not in flat
