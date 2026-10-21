"""Contract tests — payroll event schemas (T1, written before any producer).

Pins the two payroll v1 contracts ahead of the domain and worker:

1. Both schemas are valid Draft 2020-12, parse their `payroll-*.v1.json` files
   and validate the payloads pinned by `.ai/features/payroll.md` → Events.
2. The payloads carry identifiers, period and counts only. No PII-shaped key
   (`salary`, `name`, `note`, …) and no amount-like key (`amount`, `base`,
   `bonus`, `deduction`, `net`, …) can appear, and `additionalProperties:
   false` keeps producers from smuggling one in.
3. Both events pass the outbox write gate and resolve the right Kafka message
   key: `payslip.issued` by `employee_id`, run-level `payroll.calculated` by
   `run_id` (ADR 0012 and its 2026-10-21 amendment).

There are deliberately no builders here yet — T3 adds `domains/payroll/events.py`
and T6/T7 become its producers. The sample payloads below are the contract's
own fixtures.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.shared.outbox import EventEnvelope, kafka_message_key

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "payroll"
ENVELOPE_PATH = REPO_ROOT / "contracts" / "events" / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = "00000000-0000-4000-8000-000000000001"
ACTOR_ID = "00000000-0000-4000-8000-000000000002"
RUN_ID = "00000000-0000-4000-8000-000000000003"
EMPLOYEE_ID = "00000000-0000-4000-8000-000000000004"
PAYSLIP_ID = "00000000-0000-4000-8000-000000000005"
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
PERIOD = "2026-09"

SAMPLE_DATA: dict[str, dict[str, Any]] = {
    "payroll-calculated.v1.json": {
        "run_id": RUN_ID,
        "period": PERIOD,
        "employee_count": 12,
    },
    "payslip-issued.v1.json": {
        "payslip_id": PAYSLIP_ID,
        "run_id": RUN_ID,
        "employee_id": EMPLOYEE_ID,
        "period": PERIOD,
    },
}

_PII_KEY_MARKERS = ("name", "email", "phone", "salary", "reason", "note")
_AMOUNT_KEY_MARKERS = (
    "amount",
    "base",
    "bonus",
    "deduction",
    "gross",
    "net",
    "rate",
    "currency",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _event(schema_file: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Wrap one sample payload in the shared v1 envelope."""
    base, version = schema_file.removesuffix(".json").rsplit(".v", 1)
    domain, event_name = base.split("-", 1)
    return {
        "event_id": f"evt_{RUN_ID}",
        "event_type": f"{domain}.{event_name.replace('-', '_')}",
        "version": int(version),
        "tenant_id": TENANT_ID,
        "occurred_at": NOW.isoformat(),
        "actor_id": ACTOR_ID,
        "data": SAMPLE_DATA[schema_file] if data is None else data,
    }


def _matching_keys(obj: Any, markers: tuple[str, ...]) -> list[str]:
    """Return keys anywhere in a payload tree whose names match a marker."""
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if any(marker in key.lower() for marker in markers):
                found.append(key)
            found.extend(_matching_keys(value, markers))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_matching_keys(item, markers))
    return found


@pytest.fixture(scope="module")
def envelope_schema() -> dict[str, Any]:
    """The shared v1 envelope every event must validate against."""
    return _load(ENVELOPE_PATH)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per payroll event schema."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("payroll-*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    for schema_path in sorted(SCHEMAS_DIR.glob("payslip-*.v1.json")):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.name] = Draft202012Validator(schema)
    return by_schema


def test_schema_files_match_sample_cases(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Every payroll v1 schema has a sample payload."""
    assert set(validators) == set(SAMPLE_DATA)


def test_schemas_mirror_the_envelope_conventions(
    validators: dict[str, Draft202012Validator],
) -> None:
    """Draft 2020-12 and `additionalProperties: false`, like every contract."""
    assert validators
    for schema_file in SAMPLE_DATA:
        schema = _load(SCHEMAS_DIR / schema_file)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False


def test_payload_keys_match_the_pinned_events_table() -> None:
    """The spec's Events table, key for key (`.ai/features/payroll.md`)."""
    expected = {
        "payroll-calculated.v1.json": {"run_id", "period", "employee_count"},
        "payslip-issued.v1.json": {
            "payslip_id",
            "run_id",
            "employee_id",
            "period",
        },
    }
    for schema_file, data in SAMPLE_DATA.items():
        assert set(data) == expected[schema_file]
        schema = _load(SCHEMAS_DIR / schema_file)
        assert set(schema["required"]) == expected[schema_file]
        assert set(schema["properties"]) == expected[schema_file]


def test_event_type_matches_schema_file() -> None:
    """`event_type` = `<domain>.<event>` mirrors the schema filename."""
    for schema_file in SAMPLE_DATA:
        event = _event(schema_file)
        base, version = schema_file.removesuffix(".json").rsplit(".v", 1)
        domain, event_name = base.split("-", 1)
        assert event["event_type"] == f"{domain}.{event_name.replace('-', '_')}"
        assert event["version"] == int(version)


@pytest.mark.parametrize("schema_file", sorted(SAMPLE_DATA))
def test_sample_event_validates_envelope_and_payload(
    validators: dict[str, Draft202012Validator],
    envelope_schema: dict[str, Any],
    schema_file: str,
) -> None:
    """The whole event validates against the envelope and its own schema."""
    event = _event(schema_file)

    Draft202012Validator(envelope_schema).validate(event)
    validators[schema_file].validate(event["data"])


@pytest.mark.parametrize("schema_file", sorted(SAMPLE_DATA))
def test_payloads_carry_no_pii_or_amount_keys(schema_file: str) -> None:
    """Identifiers, period and counts only — never PII or money."""
    data = SAMPLE_DATA[schema_file]

    assert _matching_keys(data, _PII_KEY_MARKERS) == []
    assert _matching_keys(data, _AMOUNT_KEY_MARKERS) == []


@pytest.mark.parametrize("schema_file", sorted(SAMPLE_DATA))
def test_schemas_reject_pii_keys(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
) -> None:
    """`additionalProperties: false` rejects free text smuggled into a payload."""
    tampered = {**SAMPLE_DATA[schema_file], "note": "family matter"}
    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


@pytest.mark.parametrize("schema_file", sorted(SAMPLE_DATA))
@pytest.mark.parametrize("amount_key", _AMOUNT_KEY_MARKERS)
def test_schemas_reject_amount_keys(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    amount_key: str,
) -> None:
    """No amount field can be added to either event in v1."""
    tampered = {**SAMPLE_DATA[schema_file], amount_key: 1.23}
    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


@pytest.mark.parametrize(
    ("schema_file", "field"),
    [
        ("payroll-calculated.v1.json", "run_id"),
        ("payroll-calculated.v1.json", "period"),
        ("payroll-calculated.v1.json", "employee_count"),
        ("payslip-issued.v1.json", "payslip_id"),
        ("payslip-issued.v1.json", "run_id"),
        ("payslip-issued.v1.json", "employee_id"),
        ("payslip-issued.v1.json", "period"),
    ],
)
def test_schemas_require_every_pinned_key(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    field: str,
) -> None:
    data = dict(SAMPLE_DATA[schema_file])
    del data[field]

    with pytest.raises(ValidationError):
        validators[schema_file].validate(data)


@pytest.mark.parametrize(
    ("schema_file", "tampering"),
    [
        pytest.param(
            "payroll-calculated.v1.json",
            {"period": "2026-13"},
            id="calculated-month-out-of-range",
        ),
        pytest.param(
            "payroll-calculated.v1.json",
            {"period": "2026-9"},
            id="calculated-month-not-zero-padded",
        ),
        pytest.param(
            "payroll-calculated.v1.json",
            {"employee_count": -1},
            id="calculated-negative-count",
        ),
        pytest.param(
            "payroll-calculated.v1.json",
            {"employee_count": 1.5},
            id="calculated-fractional-count",
        ),
        pytest.param(
            "payslip-issued.v1.json",
            {"period": "2026-00"},
            id="issued-month-out-of-range",
        ),
    ],
)
def test_schemas_reject_invalid_values(
    validators: dict[str, Draft202012Validator],
    schema_file: str,
    tampering: dict[str, Any],
) -> None:
    """The period pattern, non-negative integer count and key whitelist hold."""
    tampered = {**SAMPLE_DATA[schema_file], **tampering}

    with pytest.raises(ValidationError):
        validators[schema_file].validate(tampered)


@pytest.mark.parametrize("schema_file", sorted(SAMPLE_DATA))
def test_sample_events_pass_the_outbox_write_gate(schema_file: str) -> None:
    """`EventEnvelope` accepts both contracts (ADR 0012 amendment)."""
    EventEnvelope.model_validate(_event(schema_file))


def test_kafka_message_key_rule() -> None:
    """Run-level events key by `run_id`; payslips stay employee-keyed."""
    calculated = _event("payroll-calculated.v1.json")
    issued = _event("payslip-issued.v1.json")

    assert kafka_message_key(calculated) == RUN_ID
    assert kafka_message_key(issued) == EMPLOYEE_ID
