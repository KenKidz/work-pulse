"""Contract tests — leave event builders vs. the v1 JSON schemas.

Guards the invariants the leave producers and the realtime consumer depend on:

1. The pure builders in `app.domains.leave.events` produce payloads that
   validate against `contracts/events/leave/*.v1.json` and wrap in an envelope
   that validates against `contracts/events/common/event-envelope.v1.json`.
2. No PII-shaped key — `reason` and `decision_note` included — can appear in
   any payload (`.ai/features/leave.md` → PII; `.ai/security.md`).
3. The four schemas stay exactly as the spec's Events table pins them:
   `leave.approved` carries `days` + `decided_by`, `leave.rejected` carries
   `decided_by` but no `days`, `leave.withdrawn` carries neither.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.leave import events
from app.shared.outbox import EventEnvelope

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "leave"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000003")
LEAVE_REQUEST_ID = UUID("00000000-0000-4000-8000-000000000004")
DECIDER_ID = UUID("00000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
START_DATE = date(2026, 10, 26)
END_DATE = date(2026, 10, 28)
DAYS = 3

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason", "note")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per leave event schema."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("leave-*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "leave-requested.v1.json",
        lambda: events.leave_requested(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST_ID,
            employee_id=EMPLOYEE_ID,
            leave_type_code="annual",
            start_date=START_DATE,
            end_date=END_DATE,
            days=DAYS,
        ),
    ),
    (
        "leave-approved.v1.json",
        lambda: events.leave_approved(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST_ID,
            employee_id=EMPLOYEE_ID,
            leave_type_code="annual",
            start_date=START_DATE,
            end_date=END_DATE,
            days=DAYS,
            decided_by=DECIDER_ID,
        ),
    ),
    (
        "leave-rejected.v1.json",
        lambda: events.leave_rejected(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST_ID,
            employee_id=EMPLOYEE_ID,
            leave_type_code="sick",
            start_date=START_DATE,
            end_date=END_DATE,
            decided_by=DECIDER_ID,
        ),
    ),
    (
        "leave-withdrawn.v1.json",
        lambda: events.leave_withdrawn(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            leave_request_id=LEAVE_REQUEST_ID,
            employee_id=EMPLOYEE_ID,
            start_date=START_DATE,
            end_date=END_DATE,
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
    """Every `contracts/events/leave/*.v1.json` schema has a builder case."""
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
    """No PII-shaped key (`reason`, `decision_note`, …) can appear anywhere."""
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
    tampered = {**build()["data"], "reason": "family matter"}
    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


def test_builder_event_type_matches_schema_file() -> None:
    """`event_type` = `leave.<event>` mirrors the schema filename.

    `leave-requested.v1.json` → `leave.requested` (the hyphen inside the event
    name becomes an underscore).
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
    """The spec's Events table, key for key (`.ai/features/leave.md`)."""
    expected = {
        "leave-requested.v1.json": {
            "leave_request_id",
            "employee_id",
            "leave_type_code",
            "start_date",
            "end_date",
            "days",
        },
        "leave-approved.v1.json": {
            "leave_request_id",
            "employee_id",
            "leave_type_code",
            "start_date",
            "end_date",
            "days",
            "decided_by",
        },
        "leave-rejected.v1.json": {
            "leave_request_id",
            "employee_id",
            "leave_type_code",
            "start_date",
            "end_date",
            "decided_by",
        },
        "leave-withdrawn.v1.json": {
            "leave_request_id",
            "employee_id",
            "start_date",
            "end_date",
        },
    }
    for schema_file, build in BUILDER_CASES:
        assert set(build()["data"]) == expected[schema_file]
        schema = _load(SCHEMAS_DIR / schema_file)
        assert set(schema["properties"]) == expected[schema_file]


def test_decided_by_is_the_deciding_actor_not_the_requester() -> None:
    """ADR 0010 — `decided_by` is a user id, distinct from the requester here."""
    approved = events.leave_approved(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST_ID,
        employee_id=EMPLOYEE_ID,
        leave_type_code="annual",
        start_date=START_DATE,
        end_date=END_DATE,
        days=DAYS,
        decided_by=DECIDER_ID,
    )
    assert approved["data"]["decided_by"] == str(DECIDER_ID)
    assert approved["data"]["decided_by"] != approved["data"]["employee_id"]


def test_dates_are_serialized_as_yyyy_mm_dd() -> None:
    """Dates are plain `YYYY-MM-DD` strings, never date-times."""
    for _, build in BUILDER_CASES:
        data = build()["data"]
        assert data["start_date"] == "2026-10-26"
        assert data["end_date"] == "2026-10-28"


@pytest.mark.parametrize(
    ("schema_file", "tampering"),
    [
        pytest.param(
            "leave-requested.v1.json",
            {"days": 0},
            id="requested-zero-days",
        ),
        pytest.param(
            "leave-requested.v1.json",
            {"days": "3"},
            id="requested-string-days",
        ),
        pytest.param(
            "leave-requested.v1.json",
            {"leave_type_code": "holiday"},
            id="requested-unknown-type",
        ),
        pytest.param(
            "leave-approved.v1.json",
            {"days": -1},
            id="approved-negative-days",
        ),
        pytest.param(
            "leave-withdrawn.v1.json",
            {"days": 3},
            id="withdrawn-unexpected-days",
        ),
    ],
)
def test_schemas_reject_invalid_values(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    tampering: dict[str, Any],
) -> None:
    """Types, `minimum: 1`, the type enum and the key whitelist are enforced."""
    build = dict(BUILDER_CASES)[schema_file]
    tampered = {**build()["data"], **tampering}

    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)
