"""Contract tests — performance event builders vs. the v1 JSON schemas (T3).

Guards the invariants the T5–T7 producers and later consumers depend on:

1. The pure builders in `app.domains.performance.events` produce payloads that
   validate against `contracts/events/performance/*.v1.json` and wrap in an
   envelope that validates against `contracts/events/common/event-envelope.v1.json`.
2. No PII-shaped key — goal text, review period/strengths/improvements, the
   feedback comment, skill notes or the rating — can appear in any payload
   (`.ai/features/performance.md` → PII; `.ai/security.md`).
3. The eight schemas stay exactly as the spec's Events table pins them, every
   payload carries the subject `employee_id` first for the Kafka key (ADR 0012,
   no amendment), and every payload is ids + state only.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.domains.performance import events
from app.shared.outbox import EventEnvelope, kafka_message_key

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "performance"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
GOAL_ID = UUID("00000000-0000-4000-8000-000000000003")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000004")
REVIEW_ID = UUID("00000000-0000-4000-8000-000000000005")
FEEDBACK_ID = UUID("00000000-0000-4000-8000-000000000006")
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)

_PII_KEY_MARKERS = (
    "title",
    "description",
    "period",
    "strength",
    "improvement",
    "comment",
    "note",
    "rating",
)
_PII_KEYS = (
    "title",
    "description",
    "period",
    "strengths",
    "improvements",
    "comment",
    "note",
    "rating",
)

BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "performance-goal-created.v1.json",
        lambda: events.goal_created(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            goal_id=GOAL_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-goal-activated.v1.json",
        lambda: events.goal_activated(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            goal_id=GOAL_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-goal-completed.v1.json",
        lambda: events.goal_completed(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            goal_id=GOAL_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-goal-cancelled.v1.json",
        lambda: events.goal_cancelled(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            goal_id=GOAL_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-review-started.v1.json",
        lambda: events.review_started(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            review_id=REVIEW_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-review-completed.v1.json",
        lambda: events.review_completed(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            review_id=REVIEW_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-review-cancelled.v1.json",
        lambda: events.review_cancelled(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            review_id=REVIEW_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
    (
        "performance-feedback-submitted.v1.json",
        lambda: events.feedback_submitted(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            feedback_id=FEEDBACK_ID,
            review_id=REVIEW_ID,
            employee_id=EMPLOYEE_ID,
        ),
    ),
]

# The pinned Events table, key for key (`.ai/features/performance.md` → Events).
EXPECTED_PAYLOAD_KEYS: dict[str, set[str]] = {
    "performance-goal-created.v1.json": {"goal_id", "employee_id", "status"},
    "performance-goal-activated.v1.json": {"goal_id", "employee_id", "status"},
    "performance-goal-completed.v1.json": {"goal_id", "employee_id", "status"},
    "performance-goal-cancelled.v1.json": {"goal_id", "employee_id", "status"},
    "performance-review-started.v1.json": {"review_id", "employee_id", "status"},
    "performance-review-completed.v1.json": {"review_id", "employee_id", "status"},
    "performance-review-cancelled.v1.json": {"review_id", "employee_id", "status"},
    "performance-feedback-submitted.v1.json": {
        "feedback_id",
        "review_id",
        "employee_id",
    },
}

# Every performance payload is subject-keyed: `data.employee_id` is the first
# field of the ADR 0012 fallback order, so no amendment is needed.
EXPECTED_MESSAGE_KEY = str(EMPLOYEE_ID)

EXPECTED_STATUSES: dict[str, str | None] = {
    "performance-goal-created.v1.json": "draft",
    "performance-goal-activated.v1.json": "active",
    "performance-goal-completed.v1.json": "completed",
    "performance-goal-cancelled.v1.json": "cancelled",
    "performance-review-started.v1.json": "draft",
    "performance-review-completed.v1.json": "completed",
    "performance-review-cancelled.v1.json": "cancelled",
    "performance-feedback-submitted.v1.json": None,
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per performance event schema."""
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
    """Every `contracts/events/performance/*.v1.json` schema has a builder case."""
    assert set(validators) == {schema_file for schema_file, _ in BUILDER_CASES}


def test_the_eight_pinned_event_types_are_built() -> None:
    """The spec's eight-event set is exactly the builder set (no extras)."""
    assert {build()["event_type"] for _, build in BUILDER_CASES} == {
        "performance.goal_created",
        "performance.goal_activated",
        "performance.goal_completed",
        "performance.goal_cancelled",
        "performance.review_started",
        "performance.review_completed",
        "performance.review_cancelled",
        "performance.feedback_submitted",
    }


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
    """No PII-shaped key (`title`, `strengths`, `comment`, `rating`, …) appears."""
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

    `performance-goal-created.v1.json` → `performance.goal_created` (the hyphens
    inside the event name become underscores).
    """
    for schema_file, build in BUILDER_CASES:
        event = build()
        base, version = schema_file.removesuffix(".json").rsplit(".v", 1)
        domain, event_name = base.split("-", 1)
        assert event["event_type"] == f"{domain}.{event_name.replace('-', '_')}"
        assert event["version"] == int(version)


def test_payload_keys_match_the_pinned_events_table() -> None:
    """The spec's Events table, key for key (`.ai/features/performance.md`)."""
    for schema_file, build in BUILDER_CASES:
        assert set(build()["data"]) == EXPECTED_PAYLOAD_KEYS[schema_file]
        schema = _load(SCHEMAS_DIR / schema_file)
        assert set(schema["properties"]) == EXPECTED_PAYLOAD_KEYS[schema_file]


def test_builders_pass_the_outbox_write_gate() -> None:
    """The producer's write gate accepts every builder unchanged (ADR 0012)."""
    for _, build in BUILDER_CASES:
        EventEnvelope.model_validate(build())


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_every_event_is_keyed_by_the_subject_employee(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """`data.employee_id` wins the ADR 0012 fallback — no amendment needed."""
    event = build()
    assert event["data"]["employee_id"] == EXPECTED_MESSAGE_KEY
    assert kafka_message_key(event) == EXPECTED_MESSAGE_KEY


def test_feedback_is_keyed_by_the_subject_not_the_review() -> None:
    """The feedback event carries the review's subject for per-employee order."""
    event = dict(BUILDER_CASES)["performance-feedback-submitted.v1.json"]()
    assert event["data"]["review_id"] == str(REVIEW_ID)
    assert event["data"]["feedback_id"] == str(FEEDBACK_ID)
    assert kafka_message_key(event) == str(EMPLOYEE_ID)
    assert kafka_message_key(event) != str(REVIEW_ID)


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_statuses_match_the_pinned_state(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """Each transition payload carries exactly its post-transition state."""
    expected = EXPECTED_STATUSES[schema_file]
    data = build()["data"]
    if expected is None:
        assert "status" not in data
    else:
        assert data["status"] == expected


@pytest.mark.parametrize(
    ("schema_file", "tampering"),
    [
        pytest.param(
            "performance-goal-created.v1.json",
            {"status": "active"},
            id="goal-created-not-draft",
        ),
        pytest.param(
            "performance-goal-activated.v1.json",
            {"status": "draft"},
            id="goal-activated-not-active",
        ),
        pytest.param(
            "performance-goal-completed.v1.json",
            {"status": "cancelled"},
            id="goal-completed-not-completed",
        ),
        pytest.param(
            "performance-goal-cancelled.v1.json",
            {"status": "completed"},
            id="goal-cancelled-not-cancelled",
        ),
        pytest.param(
            "performance-review-started.v1.json",
            {"status": "completed"},
            id="review-started-not-draft",
        ),
        pytest.param(
            "performance-review-completed.v1.json",
            {"status": "draft"},
            id="review-completed-not-completed",
        ),
        pytest.param(
            "performance-review-cancelled.v1.json",
            {"status": "completed"},
            id="review-cancelled-not-cancelled",
        ),
        pytest.param(
            "performance-feedback-submitted.v1.json",
            {"rating": 5},
            id="feedback-submitted-rating",
        ),
        pytest.param(
            "performance-goal-created.v1.json",
            {"goal_id": 42},
            id="goal-id-not-a-string",
        ),
    ],
)
def test_schemas_reject_invalid_values(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    tampering: dict[str, Any],
) -> None:
    """Status constants, id types and the no-PII rule are enforced."""
    build = dict(BUILDER_CASES)[schema_file]
    tampered = {**build()["data"], **tampering}

    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)
