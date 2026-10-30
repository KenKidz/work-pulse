"""T4 unit tests — Debezium change → projection-row mapping (no I/O).

Pins the wire decoding the contracts declare (epoch-day dates, decimal
strings), the D6 overtime status map, the approved-only leave rule and the D7
leave-type dictionary. Malformed images raise `CdcMappingError` naming the
field, never its value (`.ai/security.md`).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.domains.reports.schemas import OVERTIME_STATUSES
from workers.cdc_sink.mapping import (
    _OVERTIME_STATUS_MAP,
    CAPTURED_TABLES,
    DATA_TOPICS,
    HEARTBEAT_TOPIC,
    LEAVE_TYPE_TOPIC,
    CdcMappingError,
    Heartbeat,
    LeaveType,
    LeaveTypeBook,
    UnknownLeaveTypeError,
    decode_assignment,
    decode_attendance,
    decode_employee,
    decode_leave,
    decode_leave_type,
    decode_overtime,
    parse_message,
)

TENANT = UUID("00000000-0000-4000-8000-00000000000a")
TENANT_B = UUID("00000000-0000-4000-8000-00000000000b")
EMPLOYEE = UUID("00000000-0000-4000-8000-000000000001")
DEPARTMENT = UUID("00000000-0000-4000-8000-0000000000d1")
POSITION = UUID("00000000-0000-4000-8000-0000000000e1")
LOCATION = UUID("00000000-0000-4000-8000-0000000000f1")
OVERTIME = UUID("00000000-0000-4000-8000-0000000000c1")
ATTENDANCE = UUID("00000000-0000-4000-8000-0000000000c3")
LEAVE = UUID("00000000-0000-4000-8000-0000000000c4")
LEAVE_TYPE = UUID("00000000-0000-4000-8000-0000000000c5")

EPOCH = date(1970, 1, 1)
WORKDAY = date(2026, 10, 23)
EFFECTIVE_FROM = date(2026, 6, 1)
HIRED_AT = date(2026, 1, 5)
COMMIT_TS = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def _epoch_days(value: date) -> int:
    return (value - EPOCH).days


def _envelope(
    table: str,
    after: dict[str, Any] | None,
    *,
    op: str = "c",
    snapshot: object = "false",
    before: dict[str, Any] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    commit_ms = ts_ms if ts_ms is not None else int(COMMIT_TS.timestamp() * 1000)
    return {
        "before": before,
        "after": after,
        "source": {
            "ts_ms": commit_ms,
            "lsn": 24023168,
            "snapshot": snapshot,
            "db": "workpulse",
            "schema": "public",
            "table": table,
        },
        "op": op,
        "ts_ms": commit_ms + 1,
        "transaction": None,
    }


def _wire(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _employee_row() -> dict[str, Any]:
    return {
        "id": str(EMPLOYEE),
        "tenant_id": str(TENANT),
        "hired_at": _epoch_days(HIRED_AT),
        "terminated_at": None,
    }


def _assignment_row() -> dict[str, Any]:
    return {
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "department_id": str(DEPARTMENT),
        "position_id": str(POSITION),
        "location_id": str(LOCATION),
        "effective_from": _epoch_days(EFFECTIVE_FROM),
        "effective_to": None,
    }


def _attendance_row() -> dict[str, Any]:
    return {
        "id": str(ATTENDANCE),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "workday": _epoch_days(WORKDAY),
        "duration_minutes": 468,
        "late": True,
        "minutes_late": 12,
    }


def _overtime_row(status: str = "hr_verified") -> dict[str, Any]:
    return {
        "id": str(OVERTIME),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "work_date": _epoch_days(WORKDAY),
        "hours": "2.50",
        "status": status,
    }


def _leave_row(status: str = "approved") -> dict[str, Any]:
    return {
        "id": str(LEAVE),
        "tenant_id": str(TENANT),
        "employee_id": str(EMPLOYEE),
        "leave_type_id": str(LEAVE_TYPE),
        "start_date": _epoch_days(WORKDAY),
        "days": 2,
        "status": status,
    }


def _leave_type_row() -> dict[str, Any]:
    return {"id": str(LEAVE_TYPE), "tenant_id": str(TENANT), "code": "annual"}


# --- envelope --------------------------------------------------------------


def test_topic_constants_match_the_pinned_capture_scope() -> None:
    assert CAPTURED_TABLES == (
        "employees",
        "employments",
        "attendance_records",
        "overtime_requests",
        "leave_requests",
        "leave_types",
    )
    assert DATA_TOPICS[0] == "workpulse.cdc.employees"
    assert LEAVE_TYPE_TOPIC == "workpulse.cdc.leave_types"
    assert HEARTBEAT_TOPIC == "__debezium-heartbeat.workpulse.cdc"


def test_sink_constants_match_the_cdc_contracts() -> None:
    """The sink's copies of the capture scope and status map cannot drift.

    The T3 gate pins connector ↔ contracts but does not import the worker; a
    table/status added without updating these copies would otherwise be a
    silent committed skip.
    """
    root = Path(__file__).resolve().parents[4]
    envelope = json.loads((root / "contracts/cdc/change-envelope.v1.json").read_text())
    contract_tables = set(envelope["$defs"]["source"]["properties"]["table"]["enum"])
    assert set(CAPTURED_TABLES) == contract_tables
    assert set(DATA_TOPICS) == {f"workpulse.cdc.{table}" for table in contract_tables}

    connector = json.loads((root / "ops/cdc/connector.json").read_text())
    connector_tables = {
        entry.split(".", 1)[1]
        for entry in connector["config"]["table.include.list"].split(",")
    }
    assert connector_tables == contract_tables

    overtime = json.loads(
        (root / "contracts/cdc/rows/overtime_requests.v1.json").read_text()
    )
    contract_statuses = set(overtime["properties"]["status"]["enum"])
    assert set(_OVERTIME_STATUS_MAP) == contract_statuses
    assert set(_OVERTIME_STATUS_MAP.values()) <= set(OVERTIME_STATUSES)


def test_parse_change_decodes_commit_timestamp_and_snapshot() -> None:
    change = parse_message(_wire(_envelope("employees", _employee_row(), op="r")))

    assert change is not None
    assert not isinstance(change, Heartbeat)
    assert change.table == "employees"
    assert change.op == "r"
    assert change.commit_ts == COMMIT_TS
    assert change.snapshot == "false"
    assert change.snapshot_done is True
    assert change.after == _employee_row()


def test_parse_change_keeps_snapshot_in_progress_out_of_evidence() -> None:
    change = parse_message(
        _wire(_envelope("employees", _employee_row(), op="r", snapshot="true"))
    )
    assert change is not None and not isinstance(change, Heartbeat)
    assert change.snapshot == "true"
    assert change.snapshot_done is False


def test_parse_change_decodes_last_snapshot_row() -> None:
    change = parse_message(
        _wire(_envelope("employees", _employee_row(), op="r", snapshot="last"))
    )
    assert change is not None and not isinstance(change, Heartbeat)
    assert change.snapshot == "last"
    assert change.snapshot_done is True


def test_parse_change_normalises_boolean_snapshot_markers() -> None:
    change = parse_message(
        _wire(_envelope("employees", _employee_row(), op="r", snapshot=True))
    )
    assert change is not None and not isinstance(change, Heartbeat)
    assert change.snapshot == "true"


def test_parse_heartbeat() -> None:
    heartbeat = parse_message(_wire({"ts_ms": int(COMMIT_TS.timestamp() * 1000)}))
    assert isinstance(heartbeat, Heartbeat)
    assert heartbeat.commit_ts == COMMIT_TS


@pytest.mark.parametrize(
    "value",
    [
        None,
        b"not json",
        b"[]",
        _wire({"hello": "world"}),
        _wire({"ts_ms": "yesterday"}),
        _wire(_envelope("unknown_table", _employee_row())),
    ],
)
def test_parse_skips_messages_outside_the_contract(value: bytes | None) -> None:
    assert parse_message(value) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {**_envelope("employees", _employee_row()), "op": "x"}, id="bad-op"
        ),
        pytest.param(
            {**_envelope("employees", _employee_row()), "before": "nope"},
            id="bad-before",
        ),
        pytest.param(
            {
                **_envelope("employees", _employee_row()),
                "source": {
                    **_envelope("employees", _employee_row())["source"],
                    "snapshot": "maybe",
                },
            },
            id="bad-snapshot",
        ),
        pytest.param(
            {
                **_envelope("employees", _employee_row()),
                "source": {
                    key: value
                    for key, value in _envelope("employees", _employee_row())[
                        "source"
                    ].items()
                    if key != "ts_ms"
                },
            },
            id="missing-source-ts",
        ),
        pytest.param(_envelope("employees", None, op="c"), id="missing-after"),
        pytest.param(
            _envelope("employees", _employee_row(), op="u", before=None),
            id="missing-before",
        ),
    ],
)
def test_parse_rejects_a_malformed_change_naming_the_field(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(CdcMappingError) as error:
        parse_message(_wire(payload))
    message = str(error.value)
    assert "missing" in message or "invalid" in message or "not an object" in message


def test_delete_and_truncate_are_marked_dropped() -> None:
    deleted = parse_message(
        _wire(_envelope("employees", None, op="d", before=_employee_row()))
    )
    truncated = parse_message(
        _wire(_envelope("employees", None, op="t", snapshot="false"))
    )
    assert deleted is not None and not isinstance(deleted, Heartbeat)
    assert truncated is not None and not isinstance(truncated, Heartbeat)
    assert deleted.dropped is True
    assert truncated.dropped is True


# --- row decoding ----------------------------------------------------------


def test_decode_employee() -> None:
    row = decode_employee(_employee_row())
    assert row.tenant_id == TENANT
    assert row.employee_id == EMPLOYEE
    assert row.hired_at == HIRED_AT
    assert row.terminated_at is None


def test_decode_assignment_with_open_interval() -> None:
    row = decode_assignment(_assignment_row())
    assert row.effective_from == EFFECTIVE_FROM
    assert row.effective_to is None
    assert row.department_id == DEPARTMENT
    assert row.position_id == POSITION
    assert row.location_id == LOCATION


def test_decode_attendance_keeps_optional_values() -> None:
    fact = decode_attendance(_attendance_row())
    assert fact.attendance_id == ATTENDANCE
    assert fact.workday == WORKDAY
    assert fact.duration_minutes == 468
    assert fact.late is True
    assert fact.minutes_late == 12
    # The CDC path never stamps dimensions (D3).
    assert fact.department_id is None
    assert fact.hired_at is None


def test_decode_attendance_accepts_open_record_nulls() -> None:
    row = {**_attendance_row(), "duration_minutes": None, "minutes_late": None}
    fact = decode_attendance(row)
    assert fact.duration_minutes is None
    assert fact.minutes_late is None


@pytest.mark.parametrize(
    ("status", "projected"),
    [
        ("requested", "requested"),
        ("manager_approved", "manager_approved"),
        ("hr_verified", "hr_verified"),
        ("included_in_payroll", "hr_verified"),
        ("rejected", "rejected"),
        ("withdrawn", "withdrawn"),
    ],
)
def test_overtime_status_map_projects_the_fold_vocabulary(
    status: str, projected: str
) -> None:
    fact = decode_overtime(_overtime_row(status))
    assert fact.status == projected


def test_overtime_hours_are_an_exact_decimal() -> None:
    fact = decode_overtime(_overtime_row())
    assert fact.hours == Decimal("2.50")


def test_overtime_rejects_an_unknown_status() -> None:
    with pytest.raises(CdcMappingError, match="status"):
        decode_overtime(_overtime_row("paid"))


def test_leave_only_projects_approved_requests() -> None:
    book = LeaveTypeBook()
    book.apply(decode_leave_type(_leave_type_row()))

    fact = decode_leave(_leave_row("approved"), book)
    assert fact is not None
    assert fact.leave_type_code == "annual"
    assert fact.approved_day_count == 2

    for status in ("pending", "rejected", "withdrawn"):
        assert decode_leave(_leave_row(status), book) is None


def test_leave_unknown_type_retries_then_falls_back() -> None:
    book = LeaveTypeBook()
    with pytest.raises(UnknownLeaveTypeError):
        decode_leave(_leave_row(), book)

    fact = decode_leave(_leave_row(), book, allow_unknown_type=True)
    assert fact is not None
    assert fact.leave_type_code == "unknown"


@pytest.mark.parametrize("days", [0, -1, 4_294_967_296])
def test_leave_days_outside_the_projection_range_are_rejected(days: int) -> None:
    book = LeaveTypeBook()
    book.apply(decode_leave_type(_leave_type_row()))
    with pytest.raises(CdcMappingError, match="days"):
        decode_leave({**_leave_row(), "days": days}, book)


def test_extreme_ts_ms_is_a_mapping_error() -> None:
    with pytest.raises(CdcMappingError, match="ts_ms"):
        parse_message(
            _wire(
                _envelope(
                    "employees",
                    _employee_row(),
                    ts_ms=10**30,
                )
            )
        )


def test_leave_type_book_is_tenant_scoped_and_refreshes() -> None:
    book = LeaveTypeBook()
    book.apply(decode_leave_type(_leave_type_row()))

    assert book.resolve(TENANT, LEAVE_TYPE) == "annual"
    assert book.resolve(TENANT_B, LEAVE_TYPE) is None

    book.apply(LeaveType(tenant_id=TENANT, leave_type_id=LEAVE_TYPE, code="sick"))
    assert book.resolve(TENANT, LEAVE_TYPE) == "sick"
    assert len(book) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("tenant_id", "not-a-uuid", id="tenant-id"),
        pytest.param("id", "not-a-uuid", id="employee-id"),
        pytest.param("hired_at", "2026-01-05", id="date-as-string"),
        pytest.param("hired_at", True, id="date-as-bool"),
    ],
)
def test_decode_rejects_bad_wire_values_naming_the_field(
    field: str, value: object
) -> None:
    row = {**_employee_row(), field: value}
    with pytest.raises(CdcMappingError, match=field):
        decode_employee(row)


def test_decode_rejects_non_string_decimal() -> None:
    row = {**_overtime_row(), "hours": 2.5}
    with pytest.raises(CdcMappingError, match="hours"):
        decode_overtime(row)
