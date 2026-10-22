"""Contract tests — the notification worker's consumed catalog (T6).

The worker consumes the topic's whole catalog but may act on exactly five v1
event types (ADR 0015's trigger map); everything else is ignored. These tests
bind the worker's mapping to the frozen contracts:

1. Every `contracts/events/**/*.v1.json` schema has a builder case and the
   builder's envelope validates against the shared envelope schema.
2. Only the five trigger payloads produce a `NotificationIntent`; the rest of
   the catalog maps to `None` (the sweep).
3. The five trigger schemas keep the field asymmetries the templates bind to
   (`approved` has `days` + `decided_by`; `rejected` has `decided_by` only;
   `withdrawn` has neither).
4. Hostile free text (`reason`, `decision_note`) cannot reach an intent's
   allowlist or a rendered message (`.ai/security.md`, spec AC 9).
"""

import json
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from app.domains.attendance import events as attendance_events
from app.domains.employees import events as employee_events
from app.domains.leave import events as leave_events
from app.domains.notifications.models import NotificationKind
from app.domains.overtime import events as overtime_events
from workers.notifications.mapping import TRIGGER_EVENT_TYPES, map_envelope
from workers.notifications.templates import render

REPO_ROOT = Path(__file__).resolve().parents[4]
EVENTS_DIR = REPO_ROOT / "contracts" / "events"
ENVELOPE_PATH = EVENTS_DIR / "common" / "event-envelope.v1.json"

# Deterministic synthetic fixtures (.ai/testing.md rule 6) — no real people.
TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000002")
EMPLOYEE_ID = UUID("00000000-0000-4000-8000-000000000003")
LEAVE_REQUEST_ID = UUID("00000000-0000-4000-8000-000000000004")
DECIDER_ID = UUID("00000000-0000-4000-8000-000000000005")
ATTENDANCE_ID = UUID("00000000-0000-4000-8000-000000000006")
SHIFT_ID = UUID("00000000-0000-4000-8000-000000000007")
DEPARTMENT_ID = UUID("00000000-0000-4000-8000-000000000008")
POSITION_ID = UUID("00000000-0000-4000-8000-000000000009")
LOCATION_ID = UUID("00000000-0000-4000-8000-00000000000a")
PAYROLL_RUN_ID = UUID("00000000-0000-4000-8000-00000000000b")
PAYSLIP_ID = UUID("00000000-0000-4000-8000-00000000000c")
OVERTIME_ID = UUID("00000000-0000-4000-8000-00000000000d")
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
WORKDAY = date(2026, 10, 21)
END_DATE = date(2026, 10, 23)
DAYS = 3
HOURS = Decimal("1.50")
PERIOD = "2026-09"

# A hostile value planted in `data`; it must never reach an intent or message.
SENTINEL = "Family medical details — do-not-render"

_TRIGGER_KINDS: dict[str, NotificationKind] = {
    "leave.requested": NotificationKind.LEAVE_REQUESTED,
    "leave.approved": NotificationKind.LEAVE_APPROVED,
    "leave.rejected": NotificationKind.LEAVE_REJECTED,
    "leave.withdrawn": NotificationKind.LEAVE_WITHDRAWN,
    "attendance.late": NotificationKind.ATTENDANCE_LATE,
}

# The trigger-table content fields, pinned one event type at a time (F6).
_EXPECTED_CONTENT_FIELDS: dict[str, set[str]] = {
    "leave.requested": {"leave_type_code", "start_date", "end_date", "days"},
    "leave.approved": {
        "leave_type_code",
        "start_date",
        "end_date",
        "days",
        "decided_by",
    },
    "leave.rejected": {
        "leave_type_code",
        "start_date",
        "end_date",
        "decided_by",
    },
    "leave.withdrawn": {"start_date", "end_date"},
    "attendance.late": {"workday", "minutes_late"},
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _event_type(schema_path: Path) -> str:
    """`leave/leave-requested.v1.json` → `leave.requested`."""
    base, version = schema_path.name.removesuffix(".json").rsplit(".v", 1)
    assert version == "1"
    domain, event_name = base.split("-", 1)
    return f"{domain}.{event_name.replace('-', '_')}"


def _raw_envelope(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """An envelope without a domain builder — payroll's builders land in T3."""
    return {
        "event_id": f"evt_{event_type.replace('.', '_')}",
        "event_type": event_type,
        "version": 1,
        "tenant_id": str(TENANT_ID),
        "occurred_at": NOW.isoformat(),
        "actor_id": str(ACTOR_ID),
        "data": data,
    }


BUILDER_CASES: dict[str, Callable[[], dict[str, Any]]] = {
    "attendance/attendance-checked-in.v1.json": lambda: (
        attendance_events.attendance_checked_in(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            attendance_id=ATTENDANCE_ID,
            employee_id=EMPLOYEE_ID,
            workday=WORKDAY,
            work_mode="onsite",
            late=False,
            minutes_late=None,
        )
    ),
    "attendance/attendance-checked-out.v1.json": lambda: (
        attendance_events.attendance_checked_out(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            attendance_id=ATTENDANCE_ID,
            employee_id=EMPLOYEE_ID,
            workday=WORKDAY,
            duration_minutes=492,
            late=False,
            minutes_late=None,
        )
    ),
    "attendance/attendance-late.v1.json": lambda: attendance_events.attendance_late(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        attendance_id=ATTENDANCE_ID,
        employee_id=EMPLOYEE_ID,
        workday=WORKDAY,
        shift_id=SHIFT_ID,
        minutes_late=17,
    ),
    "employees/employee-created.v1.json": lambda: employee_events.employee_created(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        department_id=DEPARTMENT_ID,
        position_id=POSITION_ID,
        location_id=LOCATION_ID,
        employment_type="full_time",
    ),
    "employees/employee-updated.v1.json": lambda: employee_events.employee_updated(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        employee_id=EMPLOYEE_ID,
        changed_fields=["first_name"],
    ),
    "employees/employee-transferred.v1.json": (
        lambda: employee_events.employee_transferred(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            employee_id=EMPLOYEE_ID,
            from_department_id=DEPARTMENT_ID,
            to_department_id=POSITION_ID,
            effective_from=WORKDAY,
        )
    ),
    "employees/employee-terminated.v1.json": (
        lambda: employee_events.employee_terminated(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            employee_id=EMPLOYEE_ID,
            effective_date=WORKDAY,
        )
    ),
    "leave/leave-requested.v1.json": lambda: leave_events.leave_requested(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST_ID,
        employee_id=EMPLOYEE_ID,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=END_DATE,
        days=DAYS,
    ),
    "leave/leave-approved.v1.json": lambda: leave_events.leave_approved(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST_ID,
        employee_id=EMPLOYEE_ID,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=END_DATE,
        days=DAYS,
        decided_by=DECIDER_ID,
    ),
    "leave/leave-rejected.v1.json": lambda: leave_events.leave_rejected(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST_ID,
        employee_id=EMPLOYEE_ID,
        leave_type_code="annual",
        start_date=WORKDAY,
        end_date=END_DATE,
        decided_by=DECIDER_ID,
    ),
    "leave/leave-withdrawn.v1.json": lambda: leave_events.leave_withdrawn(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        leave_request_id=LEAVE_REQUEST_ID,
        employee_id=EMPLOYEE_ID,
        start_date=WORKDAY,
        end_date=END_DATE,
    ),
    "overtime/overtime-requested.v1.json": lambda: overtime_events.overtime_requested(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORKDAY,
        hours=HOURS,
    ),
    "overtime/overtime-approved.v1.json": lambda: overtime_events.overtime_approved(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORKDAY,
        hours=HOURS,
        approved_by=DECIDER_ID,
    ),
    "overtime/overtime-verified.v1.json": lambda: overtime_events.overtime_verified(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORKDAY,
        hours=HOURS,
        verified_by=DECIDER_ID,
    ),
    "overtime/overtime-rejected.v1.json": lambda: overtime_events.overtime_rejected(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORKDAY,
        hours=HOURS,
        rejected_by=DECIDER_ID,
        rejected_from="requested",
    ),
    "overtime/overtime-withdrawn.v1.json": lambda: overtime_events.overtime_withdrawn(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        overtime_id=OVERTIME_ID,
        employee_id=EMPLOYEE_ID,
        work_date=WORKDAY,
    ),
    "payroll/payroll-calculated.v1.json": lambda: _raw_envelope(
        "payroll.calculated",
        {
            "run_id": str(PAYROLL_RUN_ID),
            "period": PERIOD,
            "employee_count": 1,
        },
    ),
    "payroll/payslip-issued.v1.json": lambda: _raw_envelope(
        "payslip.issued",
        {
            "payslip_id": str(PAYSLIP_ID),
            "run_id": str(PAYROLL_RUN_ID),
            "employee_id": str(EMPLOYEE_ID),
            "period": PERIOD,
        },
    ),
}

_TRIGGER_CASES = [
    case
    for case in BUILDER_CASES
    if _event_type(EVENTS_DIR / case) in TRIGGER_EVENT_TYPES
]


@pytest.fixture(scope="module")
def envelope_validator() -> Draft202012Validator:
    """The shared v1 envelope every topic message must validate against."""
    schema = _load(ENVELOPE_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    """One checked Draft 2020-12 validator per catalog schema (by relative path)."""
    by_schema: dict[str, Draft202012Validator] = {}
    for schema_path in sorted(EVENTS_DIR.glob("*/*.v1.json")):
        if schema_path.parent.name == "common":
            continue  # the shared envelope, validated by its own fixture
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        by_schema[schema_path.relative_to(EVENTS_DIR).as_posix()] = (
            Draft202012Validator(schema)
        )
    return by_schema


def test_every_catalog_schema_has_a_builder_case(
    validators: dict[str, Draft202012Validator],
) -> None:
    """The sweep covers the whole frozen catalog, so nothing is untested."""
    assert set(validators) == set(BUILDER_CASES)


@pytest.mark.parametrize(
    ("schema_key", "build"),
    BUILDER_CASES.items(),
    ids=list(BUILDER_CASES),
)
def test_catalog_event_validates_and_only_triggers_map(
    envelope_validator: Draft202012Validator,
    validators: dict[str, Draft202012Validator],
    schema_key: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """Every catalog event validates; exactly the five triggers map to an intent."""
    event = build()
    event_type = _event_type(EVENTS_DIR / schema_key)
    assert event["event_type"] == event_type

    envelope_validator.validate(event)
    validators[schema_key].validate(event["data"])

    intent = map_envelope(event)
    if event_type not in TRIGGER_EVENT_TYPES:
        assert intent is None
        return

    assert intent is not None
    assert intent.kind is _TRIGGER_KINDS[event_type]
    assert intent.tenant_id == TENANT_ID
    assert intent.content.subject_employee_id == EMPLOYEE_ID


@pytest.mark.parametrize(
    ("schema_key", "build"),
    [(case, BUILDER_CASES[case]) for case in _TRIGGER_CASES],
    ids=_TRIGGER_CASES,
)
def test_trigger_intents_carry_exactly_the_trigger_table_content(
    schema_key: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """The allowlist holds the pinned fields and nothing else at value-time."""
    event = build()
    intent = map_envelope(event)
    assert intent is not None

    populated = {
        field.name
        for field in fields(intent.content)
        if getattr(intent.content, field.name) is not None
    }
    assert (
        populated
        == {"subject_employee_id"} | _EXPECTED_CONTENT_FIELDS[event["event_type"]]
    )


def test_only_the_five_trigger_payloads_produce_intents() -> None:
    """The catalog sweep is the AC's 'no other event type produces rows' proof."""
    produced = {
        _event_type(EVENTS_DIR / schema_key): map_envelope(build()) is not None
        for schema_key, build in BUILDER_CASES.items()
    }
    assert {event_type for event_type, mapped in produced.items() if mapped} == set(
        TRIGGER_EVENT_TYPES
    )


def test_trigger_contracts_keep_the_pinned_field_asymmetry() -> None:
    """F6: approved has `days`, rejected only `decided_by`, withdrawn neither."""
    expected = {
        "leave/leave-requested.v1.json": {
            "leave_request_id",
            "employee_id",
            "leave_type_code",
            "start_date",
            "end_date",
            "days",
        },
        "leave/leave-approved.v1.json": {
            "leave_request_id",
            "employee_id",
            "leave_type_code",
            "start_date",
            "end_date",
            "days",
            "decided_by",
        },
        "leave/leave-rejected.v1.json": {
            "leave_request_id",
            "employee_id",
            "leave_type_code",
            "start_date",
            "end_date",
            "decided_by",
        },
        "leave/leave-withdrawn.v1.json": {
            "leave_request_id",
            "employee_id",
            "start_date",
            "end_date",
        },
        "attendance/attendance-late.v1.json": {
            "attendance_id",
            "employee_id",
            "workday",
            "shift_id",
            "minutes_late",
        },
    }
    for schema_key, properties in expected.items():
        schema = _load(EVENTS_DIR / schema_key)
        assert set(schema["properties"]) == properties


@pytest.mark.parametrize(
    ("schema_key", "build"),
    [(case, BUILDER_CASES[case]) for case in _TRIGGER_CASES],
    ids=_TRIGGER_CASES,
)
def test_hostile_free_text_never_reaches_the_intent_or_message(
    schema_key: str,
    build: Callable[[], dict[str, Any]],
) -> None:
    """`reason`/`decision_note` planted on a payload are structurally discarded."""
    event = build()
    event["data"]["reason"] = SENTINEL
    event["data"]["decision_note"] = SENTINEL

    intent = map_envelope(event)
    assert intent is not None
    for field in fields(intent.content):
        assert SENTINEL not in str(getattr(intent.content, field.name))

    message = render(
        intent,
        subject_name="Ada Lovelace",
        recipient_name="Mary Manager",
        actor_name="Grace Hopper",
    )
    assert SENTINEL not in message.subject
    assert SENTINEL not in message.body
