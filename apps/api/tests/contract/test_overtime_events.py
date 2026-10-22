"""Contract tests — overtime event builders vs. the v1 JSON schemas.

Guards the invariants the overtime producers and payroll/notification consumers
depend on:

1. The pure builders in `app.domains.overtime.events` produce payloads that
   validate against `contracts/events/overtime/*.v1.json` and wrap in an
   envelope that validates against `contracts/events/common/event-envelope.v1.json`.
2. No PII-shaped key — `reason`, `manager_note`, `hr_note` included — can appear
   in any payload (`.ai/features/overtime.md` → PII; `.ai/security.md`).
3. The five schemas stay exactly as the spec's Events table pins them:
   `overtime.approved` carries `approved_by`, `overtime.verified` carries
   `verified_by`, `overtime.rejected` carries `rejected_by` +
   `rejected_from`, and `overtime.withdrawn` carries no `hours`.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.overtime import events
from app.shared.outbox import EventEnvelope

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "overtime"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000003")
OVERTIME_ID = UUID("00000000-0000-4000-8000-000000000004")
DECIDER_ID = UUID("00000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
WORK_DATE = date(2026, 10, 19)
HOURS = Decimal("1.50")

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason", "note")
_PII_KEYS = ("reason", "manager_note", "hr_note")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per overtime event schema."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("overtime-*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "overtime-requested.v1.json",
        lambda: events.overtime_requested(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            overtime_id=OVERTIME_ID,
            employee_id=EMPLOYEE_ID,
            work_date=WORK_DATE,
            hours=HOURS,
        ),
    ),
    (
        "overtime-approved.v1.json",
        lambda: events.overtime_approved(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            overtime_id=OVERTIME_ID,
            employee_id=EMPLOYEE_ID,
            work_date=WORK_DATE,
            hours=HOURS,
            approved_by=DECIDER_ID,
        ),
    ),
    (
        "overtime-verified.v1.json",
        lambda: events.overtime_verified(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            overtime_id=OVERTIME_ID,
            employee_id=EMPLOYEE_ID,
            work_date=WORK_DATE,
            hours=HOURS,
            verified_by=DECIDER_ID,
        ),
    ),
    (
        "overtime-rejected.v1.json",
        lambda: events.overtime_rejected(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            overtime_id=OVERTIME_ID,
            employee_id=EMPLOYEE_ID,
            work_date=WORK_DATE,
            hours=HOURS,
            rejected_by=DECIDER_ID,
            rejected_from="hr_verified",
        ),
    ),
    (
        "overtime-withdrawn.v1.json",
        lambda: events.overtime_withdrawn(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            overtime_id=OVERTIME_ID,
            employee_id=EMPLOYEE_ID,
            work_date=WORK_DATE,
        ),
    ),
]


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
    """Every `contracts/events/overtime/*.v1.json` schema has a builder case."""
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


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_builder_output_carries_no_pii(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """No PII-shaped key (`reason`, `manager_note`, `hr_note`, …) can appear."""
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
    """`additionalProperties: false` rejects free text smuggled into a payload."""
    for pii_key in _PII_KEYS:
        tampered = {**build()["data"], pii_key: "sensitive text"}
        with pytest.raises(ValidationError):
            validators[schema_file].validate(tampered)


def test_builder_event_type_matches_schema_file() -> None:
    """`event_type` = `overtime.<event>` mirrors the schema filename.

    `overtime-requested.v1.json` → `overtime.requested` (the hyphen inside the
    event name becomes an underscore).
    """
    for schema_file, build in BUILDER_CASES:
        event = build()
        base, version = schema_file.removesuffix(".json").rsplit(".v", 1)
        domain, event_name = base.split("-", 1)
        assert event["event_type"] == f"{domain}.{event_name.replace('-', '_')}"
        assert event["version"] == int(version)


def test_every_payload_carries_the_kafka_key() -> None:
    """`data.employee_id` is mandatory — it is the Kafka message key (ADR 0012)."""
    for _, build in BUILDER_CASES:
        assert build()["data"]["employee_id"] == str(EMPLOYEE_ID)


def test_builders_pass_the_outbox_write_gate() -> None:
    """The producer's write gate accepts every builder unchanged (ADR 0012).

    `EventEnvelope` mirrors the envelope schema and additionally requires
    `data.employee_id`; a builder that fails here would fail the request
    transaction at write time instead of at publish time.
    """
    for _, build in BUILDER_CASES:
        EventEnvelope.model_validate(build())


def test_payload_keys_match_the_pinned_events_table() -> None:
    """The spec's Events table, key for key (`.ai/features/overtime.md`)."""
    expected = {
        "overtime-requested.v1.json": {
            "overtime_id",
            "employee_id",
            "work_date",
            "hours",
        },
        "overtime-approved.v1.json": {
            "overtime_id",
            "employee_id",
            "work_date",
            "hours",
            "approved_by",
        },
        "overtime-verified.v1.json": {
            "overtime_id",
            "employee_id",
            "work_date",
            "hours",
            "verified_by",
        },
        "overtime-rejected.v1.json": {
            "overtime_id",
            "employee_id",
            "work_date",
            "hours",
            "rejected_by",
            "rejected_from",
        },
        "overtime-withdrawn.v1.json": {
            "overtime_id",
            "employee_id",
            "work_date",
        },
    }
    for schema_file, build in BUILDER_CASES:
        assert set(build()["data"]) == expected[schema_file]
        schema = _load(SCHEMAS_DIR / schema_file)
        assert set(schema["properties"]) == expected[schema_file]


def test_decision_actors_are_the_deciding_actor_not_the_requester() -> None:
    """ADR 0010 — decision actor ids are user ids, distinct from the requester."""
    approved = events.overtime_approved(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORK_DATE,
        hours=HOURS,
        approved_by=DECIDER_ID,
    )
    verified = events.overtime_verified(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORK_DATE,
        hours=HOURS,
        verified_by=DECIDER_ID,
    )
    rejected = events.overtime_rejected(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORK_DATE,
        hours=HOURS,
        rejected_by=DECIDER_ID,
        rejected_from="requested",
    )

    assert approved["data"]["approved_by"] == str(DECIDER_ID)
    assert verified["data"]["verified_by"] == str(DECIDER_ID)
    assert rejected["data"]["rejected_by"] == str(DECIDER_ID)
    for event in (approved, verified, rejected):
        assert event["data"]["employee_id"] != str(DECIDER_ID)


@pytest.mark.parametrize(
    "rejected_from", ["requested", "manager_approved", "hr_verified"]
)
def test_rejected_from_covers_every_open_state(
    validators: dict[str, Draft202012Validator],
    rejected_from: str,
) -> None:
    """All three source states validate — including the ADR 0018 un-verify window."""
    event = events.overtime_rejected(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORK_DATE,
        hours=HOURS,
        rejected_by=DECIDER_ID,
        rejected_from=cast(events.RejectedFrom, rejected_from),
    )
    validators["overtime-rejected.v1.json"].validate(event["data"])
    assert event["data"]["rejected_from"] == rejected_from


def test_dates_are_serialized_as_yyyy_mm_dd() -> None:
    """Dates are plain `YYYY-MM-DD` strings, never date-times."""
    for _, build in BUILDER_CASES:
        assert build()["data"]["work_date"] == "2026-10-19"


def test_hours_is_a_json_number_with_two_decimals() -> None:
    """ADR 0017 decision 5 — hours is a JSON number, never a formatted string."""
    for schema_file, build in BUILDER_CASES:
        data = build()["data"]
        if schema_file == "overtime-withdrawn.v1.json":
            assert "hours" not in data
            continue
        assert isinstance(data["hours"], float)
        assert data["hours"] == 1.5


@pytest.mark.parametrize(
    ("schema_file", "tampering"),
    [
        pytest.param(
            "overtime-requested.v1.json",
            {"hours": 0},
            id="requested-zero-hours",
        ),
        pytest.param(
            "overtime-requested.v1.json",
            {"hours": 12.01},
            id="requested-hours-above-cap",
        ),
        pytest.param(
            "overtime-requested.v1.json",
            {"hours": "1.5"},
            id="requested-string-hours",
        ),
        pytest.param(
            "overtime-approved.v1.json",
            {"hours": -1},
            id="approved-negative-hours",
        ),
        pytest.param(
            "overtime-rejected.v1.json",
            {"rejected_from": "included_in_payroll"},
            id="rejected-terminal-source",
        ),
        pytest.param(
            "overtime-withdrawn.v1.json",
            {"hours": 1.5},
            id="withdrawn-unexpected-hours",
        ),
    ],
)
def test_schemas_reject_invalid_values(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    tampering: dict[str, Any],
) -> None:
    """Numeric bounds, the source-state enum and the key whitelist are enforced."""
    build = dict(BUILDER_CASES)[schema_file]
    tampered = {**build()["data"], **tampering}

    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)
