"""Contract tests — recruitment event builders vs. the v1 JSON schemas.

Guards the invariants the T2–T5 producers and later consumers depend on:

1. The pure builders in `app.domains.recruitment.events` produce payloads that
   validate against `contracts/events/recruitment/*.v1.json` and wrap in an
   envelope that validates against `contracts/events/common/event-envelope.v1.json`.
2. No PII-shaped key — names, email, phone, notes, ratings, reasons — can appear
   in any payload (`.ai/features/recruitment.md` → PII; `.ai/security.md`).
3. The ten schemas stay exactly as the spec's Events table pins them, including
   the actor/employee distinction and the pinned Kafka key fallback (ADR 0012
   amendment, 2026-10-23).
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.recruitment import events
from app.shared.outbox import EventEnvelope, kafka_message_key

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "recruitment"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
JOB_ID = UUID("00000000-0000-4000-8000-000000000003")
CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000004")
INTERVIEW_ID = UUID("00000000-0000-4000-8000-000000000005")
INTERVIEWER_ID = UUID("00000000-0000-4000-8000-000000000006")
HIRED_EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000007")
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-000000000008")
POSITION_ID = UUID("00000000-0000-4000-8000-000000000009")
LOCATION_ID = UUID("00000000-0000-4000-8000-00000000000a")
DECIDER_ID = UUID("00000000-0000-4000-8000-00000000000b")
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
OPENINGS = 2

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason", "note", "rating")
_PII_KEYS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "notes",
    "rating",
    "reason",
    "note",
)

BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "job-opened.v1.json",
        lambda: events.job_opened(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            job_id=JOB_ID,
            position_id=POSITION_ID,
            department_id=DEPARTMENT_ID,
            location_id=LOCATION_ID,
            openings=OPENINGS,
        ),
    ),
    (
        "job-closed.v1.json",
        lambda: events.job_closed(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            job_id=JOB_ID,
            closed_by=DECIDER_ID,
        ),
    ),
    (
        "candidate-created.v1.json",
        lambda: events.candidate_created(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            source="referral",
        ),
    ),
    (
        "candidate-advanced.v1.json",
        lambda: events.candidate_advanced(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            from_stage="applied",
            to_stage="interviewing",
        ),
    ),
    (
        "candidate-rejected.v1.json",
        lambda: events.candidate_rejected(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            rejected_from="interviewing",
            rejected_by=DECIDER_ID,
        ),
    ),
    (
        "candidate-withdrawn.v1.json",
        lambda: events.candidate_withdrawn(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            withdrawn_from="offered",
        ),
    ),
    (
        "candidate-hired.v1.json",
        lambda: events.candidate_hired(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            employee_id=HIRED_EMPLOYEE_ID,
            hired_by=DECIDER_ID,
        ),
    ),
    (
        "interview-scheduled.v1.json",
        lambda: events.interview_scheduled(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            interview_id=INTERVIEW_ID,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            interviewer_id=INTERVIEWER_ID,
        ),
    ),
    (
        "interview-completed.v1.json",
        lambda: events.interview_completed(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            interview_id=INTERVIEW_ID,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            interviewer_id=INTERVIEWER_ID,
            completed_by=DECIDER_ID,
        ),
    ),
    (
        "interview-cancelled.v1.json",
        lambda: events.interview_cancelled(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            interview_id=INTERVIEW_ID,
            candidate_id=CANDIDATE_ID,
            job_id=JOB_ID,
            cancelled_by=DECIDER_ID,
        ),
    ),
]

# The pinned Events table, key for key (`.ai/features/recruitment.md` → Events).
EXPECTED_PAYLOAD_KEYS: dict[str, set[str]] = {
    "job-opened.v1.json": {
        "job_id",
        "position_id",
        "department_id",
        "location_id",
        "openings",
    },
    "job-closed.v1.json": {"job_id", "closed_by"},
    "candidate-created.v1.json": {"candidate_id", "job_id", "source"},
    "candidate-advanced.v1.json": {"candidate_id", "job_id", "from", "to"},
    "candidate-rejected.v1.json": {
        "candidate_id",
        "job_id",
        "rejected_from",
        "rejected_by",
    },
    "candidate-withdrawn.v1.json": {
        "candidate_id",
        "job_id",
        "withdrawn_from",
    },
    "candidate-hired.v1.json": {
        "candidate_id",
        "job_id",
        "employee_id",
        "hired_by",
    },
    "interview-scheduled.v1.json": {
        "interview_id",
        "candidate_id",
        "job_id",
        "interviewer_id",
    },
    "interview-completed.v1.json": {
        "interview_id",
        "candidate_id",
        "job_id",
        "interviewer_id",
        "completed_by",
    },
    "interview-cancelled.v1.json": {
        "interview_id",
        "candidate_id",
        "job_id",
        "cancelled_by",
    },
}

# The first present field of the ADR 0012 fallback order for each builder.
EXPECTED_MESSAGE_KEYS: dict[str, str] = {
    "job-opened.v1.json": str(JOB_ID),
    "job-closed.v1.json": str(JOB_ID),
    "candidate-created.v1.json": str(CANDIDATE_ID),
    "candidate-advanced.v1.json": str(CANDIDATE_ID),
    "candidate-rejected.v1.json": str(CANDIDATE_ID),
    "candidate-withdrawn.v1.json": str(CANDIDATE_ID),
    "candidate-hired.v1.json": str(HIRED_EMPLOYEE_ID),
    "interview-scheduled.v1.json": str(CANDIDATE_ID),
    "interview-completed.v1.json": str(CANDIDATE_ID),
    "interview-cancelled.v1.json": str(CANDIDATE_ID),
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per recruitment event schema."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


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
    """Every `contracts/events/recruitment/*.v1.json` schema has a builder case."""
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

    Draft202012Validator(envelope_schema).validate(event)
    validators[schema_file].validate(event["data"])


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_builder_output_carries_no_pii(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """No PII-shaped key (`first_name`, `email`, `notes`, `rating`, …) appears."""
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


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_schema_requires_every_pinned_field(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """Every pinned payload key is `required` — dropping one fails validation."""
    data = build()["data"]
    for field in data:
        missing = {key: value for key, value in data.items() if key != field}
        with pytest.raises(ValidationError):
            validators[schema_file].validate(missing)


def test_builder_event_type_matches_schema_file() -> None:
    """`event_type` = `<prefix>.<event>` mirrors the schema filename.

    `candidate-advanced.v1.json` → `candidate.advanced` (the hyphen inside the
    event name becomes an underscore).
    """
    for schema_file, build in BUILDER_CASES:
        event = build()
        base, version = schema_file.removesuffix(".json").rsplit(".v", 1)
        domain, event_name = base.split("-", 1)
        assert event["event_type"] == f"{domain}.{event_name.replace('-', '_')}"
        assert event["version"] == int(version)


def test_payload_keys_match_the_pinned_events_table() -> None:
    """The spec's Events table, key for key (`.ai/features/recruitment.md`)."""
    for schema_file, build in BUILDER_CASES:
        assert set(build()["data"]) == EXPECTED_PAYLOAD_KEYS[schema_file]
        schema = _load(SCHEMAS_DIR / schema_file)
        assert set(schema["properties"]) == EXPECTED_PAYLOAD_KEYS[schema_file]


def test_builders_pass_the_outbox_write_gate() -> None:
    """The producer's write gate accepts every builder unchanged (ADR 0012)."""
    for _, build in BUILDER_CASES:
        EventEnvelope.model_validate(build())


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_message_keys_follow_the_pinned_fallback_order(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """Recruitment keys resolve through the 2026-10-23 fallback order."""
    assert kafka_message_key(build()) == EXPECTED_MESSAGE_KEYS[schema_file]


def test_candidate_hired_prefers_employee_id_over_candidate_id() -> None:
    """The hire lands on the new employee's partition, like other employee facts."""
    event = dict(BUILDER_CASES)["candidate-hired.v1.json"]()
    assert event["data"]["employee_id"] == str(HIRED_EMPLOYEE_ID)
    assert event["data"]["candidate_id"] == str(CANDIDATE_ID)
    assert kafka_message_key(event) == str(HIRED_EMPLOYEE_ID)


def test_interview_events_order_with_their_candidate_not_the_interviewer() -> None:
    """`candidate_id` precedes `job_id`/`interview_id` in the fallback order."""
    for schema_file in (
        "interview-scheduled.v1.json",
        "interview-completed.v1.json",
        "interview-cancelled.v1.json",
    ):
        event = dict(BUILDER_CASES)[schema_file]()
        assert event["data"]["candidate_id"] == str(CANDIDATE_ID)
        assert event["data"]["interview_id"] == str(INTERVIEW_ID)
        assert kafka_message_key(event) == str(CANDIDATE_ID)
        assert kafka_message_key(event) != str(INTERVIEWER_ID)


def test_actor_ids_are_user_ids_and_interviewer_is_an_employee_id() -> None:
    """ADR 0010 — `*_by` fields are actor users; `interviewer_id` is a person."""
    completed = dict(BUILDER_CASES)["interview-completed.v1.json"]()
    assert completed["data"]["completed_by"] == str(DECIDER_ID)
    assert completed["data"]["interviewer_id"] == str(INTERVIEWER_ID)
    assert completed["actor_id"] == str(ACTOR_ID)

    hired = dict(BUILDER_CASES)["candidate-hired.v1.json"]()
    assert hired["data"]["hired_by"] == str(DECIDER_ID)
    assert hired["data"]["employee_id"] == str(HIRED_EMPLOYEE_ID)


@pytest.mark.parametrize(
    ("schema_file", "tampering"),
    [
        pytest.param(
            "job-opened.v1.json",
            {"openings": 0},
            id="job-opened-zero-openings",
        ),
        pytest.param(
            "job-opened.v1.json",
            {"openings": "2"},
            id="job-opened-string-openings",
        ),
        pytest.param(
            "candidate-created.v1.json",
            {"source": "linkedin"},
            id="candidate-created-unknown-source",
        ),
        pytest.param(
            "candidate-advanced.v1.json",
            {"to": "hired"},
            id="candidate-advanced-skips-to-hired",
        ),
        pytest.param(
            "candidate-advanced.v1.json",
            {"from": "offered"},
            id="candidate-advanced-from-terminal-stage",
        ),
        pytest.param(
            "candidate-rejected.v1.json",
            {"rejected_from": "hired"},
            id="candidate-rejected-from-hired",
        ),
        pytest.param(
            "candidate-withdrawn.v1.json",
            {"withdrawn_from": "rejected"},
            id="candidate-withdrawn-from-terminal-stage",
        ),
    ],
)
def test_schemas_reject_invalid_values(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    tampering: dict[str, Any],
) -> None:
    """The opening bound, source enum and stage enums are enforced."""
    build = dict(BUILDER_CASES)[schema_file]
    tampered = {**build()["data"], **tampering}

    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


def test_openings_is_a_json_integer() -> None:
    """`openings` is a whole count, never a formatted string."""
    data = dict(BUILDER_CASES)["job-opened.v1.json"]()["data"]
    assert isinstance(data["openings"], int)
    assert data["openings"] == OPENINGS
