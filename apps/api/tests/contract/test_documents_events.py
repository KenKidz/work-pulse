"""Contract tests — document event builders vs. the v1 JSON schemas (T5).

The two schemas landed in T1, before any producer. These tests bind the T4
builders to them:

1. Each builder's payload validates against `contracts/events/documents/*.v1.json`
   and wraps in an envelope that validates against the shared envelope schema.
2. Payloads are identifiers + category only: no title, filename, storage key or
   presigned URL key can appear (`.ai/security.md`).
3. Every payload carries `employee_id`, so the Kafka message key resolves
   through ADR 0012's first fallback with no amendment.
4. Every schema in the directory has a builder case — nothing untested.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from app.domains.documents import events
from app.domains.documents.schemas import DocumentCategory
from app.shared.outbox import EventEnvelope, kafka_message_key

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "documents"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000003")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

EXPECTED_DATA_KEYS = {"document_id", "employee_id", "category"}
_PII_KEY_MARKERS = (
    "title",
    "filename",
    "file_name",
    "key",
    "url",
    "storage",
    "path",
)

BUILDER_CASES: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    (
        "document-uploaded.v1.json",
        lambda: events.document_uploaded(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            document_id=DOCUMENT_ID,
            employee_id=EMPLOYEE_ID,
            category=DocumentCategory.CONTRACT,
        ),
    ),
    (
        "document-deleted.v1.json",
        lambda: events.document_deleted(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            document_id=DOCUMENT_ID,
            employee_id=EMPLOYEE_ID,
            category=DocumentCategory.CERTIFICATION,
        ),
    ),
]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _pii_keys(obj: Any) -> list[str]:
    """Return keys in the payload tree whose names look PII/secret-shaped."""
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


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


def test_schema_files_match_builder_cases(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Every `contracts/events/documents/*.v1.json` schema has a builder case."""
    assert set(validators) == {schema_file for schema_file, _ in BUILDER_CASES}


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_builder_output_validates_contract(
    validators: dict[str, Draft202012Validator],
    envelope_schema: dict[str, Any],
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    event = build()

    Draft202012Validator(envelope_schema).validate(event)
    validators[schema_file].validate(event["data"])
    EventEnvelope.model_validate(event)


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_builder_output_is_identifiers_and_category_only(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    event = build()

    assert set(event["data"]) == EXPECTED_DATA_KEYS
    assert event["data"]["document_id"] == str(DOCUMENT_ID)
    assert event["data"]["employee_id"] == str(EMPLOYEE_ID)
    assert event["data"]["category"] in {
        category.value for category in DocumentCategory
    }
    assert _pii_keys(event) == [], f"PII/secret-shaped key in {schema_file}"


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_kafka_message_key_is_the_employee_id(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    assert kafka_message_key(build()) == str(EMPLOYEE_ID)


@pytest.mark.parametrize(("schema_file", "build"), BUILDER_CASES)
def test_envelope_is_complete(
    schema_file: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    event = build()

    assert set(event) == {
        "event_id",
        "event_type",
        "version",
        "tenant_id",
        "occurred_at",
        "actor_id",
        "data",
    }
    assert event["version"] == 1
    assert event["tenant_id"] == str(TENANT_ID)
    assert event["actor_id"] == str(ACTOR_ID)
    assert datetime.fromisoformat(event["occurred_at"]) == NOW
    assert event["event_id"].startswith("evt_")


def test_schemas_reject_pii_and_unknown_keys(
    validators: dict[str, Draft202012Validator],
) -> None:
    """`additionalProperties: false` keeps a future leak from sneaking in."""
    payload = {
        "document_id": str(DOCUMENT_ID),
        "employee_id": str(EMPLOYEE_ID),
        "category": "contract",
        "title": "Secret contract",
        "filename": "secret.pdf",
    }
    for validator in validators.values():
        assert not validator.is_valid(payload)
