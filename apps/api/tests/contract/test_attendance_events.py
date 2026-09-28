"""Contract tests — attendance event builders vs. the v1 JSON schemas.

Guards the two Phase-1 invariants that Phase 3 depends on:

1. The pure builders in `app.domains.attendance.events` produce payloads that
   validate against `contracts/events/attendance/*.v1.json` and wrap in an
   envelope that validates against
   `contracts/events/common/event-envelope.v1.json`.
2. No PII-shaped key (name/email/phone/salary/reason/**note**) can appear in
   any payload — notes are free text and treated like PII
   (`.ai/features/attendance.md`, spec Risk 8).
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.attendance import events

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "attendance"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
ATTENDANCE_ID = UUID("00000000-0000-4000-8000-000000000003")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000004")
SHIFT_ID = UUID("00000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
WORKDAY = date(2026, 9, 28)

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason", "note")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per attendance event schema."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("attendance-*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "attendance-checked-in.v1.json",
        lambda: events.attendance_checked_in(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            attendance_id=ATTENDANCE_ID,
            employee_id=EMPLOYEE_ID,
            workday=WORKDAY,
            work_mode="onsite",
            late=False,
        ),
    ),
    (
        "attendance-checked-out.v1.json",
        lambda: events.attendance_checked_out(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            attendance_id=ATTENDANCE_ID,
            employee_id=EMPLOYEE_ID,
            workday=WORKDAY,
            duration_minutes=492,
        ),
    ),
    (
        "attendance-late.v1.json",
        lambda: events.attendance_late(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            attendance_id=ATTENDANCE_ID,
            employee_id=EMPLOYEE_ID,
            workday=WORKDAY,
            shift_id=SHIFT_ID,
            minutes_late=17,
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
    """Every `contracts/events/attendance/*.v1.json` schema has a builder case."""
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
    """No PII-shaped key (notes included) can appear anywhere in a payload."""
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
    tampered = {**build()["data"], "check_in_note": "late bus"}
    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


def test_builder_event_type_matches_schema_file() -> None:
    """`event_type` = `<domain>.<event>` mirrors the schema filename.

    `attendance-checked-in.v1.json` → `attendance.checked_in` (hyphens inside
    the event name become underscores; only the first hyphen is the domain
    separator).
    """
    for schema_file, build in BUILDER_CASES:
        event = build()
        base, version = schema_file.removesuffix(".json").rsplit(".v", 1)
        domain, event_name = base.split("-", 1)
        assert event["event_type"] == f"{domain}.{event_name.replace('-', '_')}"
        assert event["version"] == int(version)


def test_late_payload_is_identical_to_its_checked_in_fact() -> None:
    """The lateness event names the same record as the check-in it accompanies."""
    checked_in = events.attendance_checked_in(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=EMPLOYEE_ID,
        workday=WORKDAY,
        work_mode="remote",
        late=True,
    )
    late = events.attendance_late(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=EMPLOYEE_ID,
        workday=WORKDAY,
        shift_id=SHIFT_ID,
        minutes_late=0,  # inside the first minute past start + grace
    )
    assert late["data"]["attendance_id"] == checked_in["data"]["attendance_id"]
    assert late["data"]["employee_id"] == checked_in["data"]["employee_id"]
    assert late["data"]["workday"] == checked_in["data"]["workday"]
