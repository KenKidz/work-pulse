"""Pure Debezium change → projection-row mapping for the CDC sink (T4).

The sink consumes `workpulse.cdc.<table>` (one topic per captured table,
`contracts/cdc/`) and lands the five ADR 0019 projection tables. This module is
the whole translation layer: strict envelope parsing, Debezium wire decoding
(epoch-day dates, decimal strings) and the projection's status/reference rules.

It never touches I/O and never re-implements a domain rule that the OLTP row
does not already carry (`.ai/features/change-data-capture.md` rule 5): the
fattest mapping here is the overtime status projection (D6) and the leave-type
code lookup (D7). Rows carry identifiers, dates, counts and state only — no
names, emails, phones, notes or reasons have a field to land in
(`.ai/security.md`).

Malformed envelopes raise `CdcMappingError`; the consumer turns that into an
ids-only, committed skip (same contract as the reports consumer). An unresolvable
leave-type reference raises `UnknownLeaveTypeError`, which the consumer retries
head-of-line before using the bounded `unknown` fallback.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.domains.reports.schemas import (
    AssignmentRow,
    AttendanceFact,
    EmployeeRow,
    LeaveFact,
    OvertimeFact,
)

#: Feature rule 4 / D8 — exactly the captured tables.
CAPTURED_TABLES: tuple[str, ...] = (
    "employees",
    "employments",
    "attendance_records",
    "overtime_requests",
    "leave_requests",
    "leave_types",
)

CDC_TOPIC_PREFIX = "workpulse.cdc"
#: Debezium's heartbeat topic (SMTs do not rewrite it) — subscription listener
#: and the empty-snapshot readiness signal (D5).
HEARTBEAT_TOPIC = f"__debezium-heartbeat.{CDC_TOPIC_PREFIX}"
DATA_TOPICS: tuple[str, ...] = tuple(
    f"{CDC_TOPIC_PREFIX}.{table}" for table in CAPTURED_TABLES
)
LEAVE_TYPE_TOPIC = f"{CDC_TOPIC_PREFIX}.leave_types"

#: The projection's overtime vocabulary (folds/spec D6). `included_in_payroll`
#: is an OLTP-only terminal state: payroll's inclusion flip emits no event, so
#: the fold never moved past `hr_verified`; the CDC row projects it the same
#: way and the reports filter on `hr_verified`.
_OVERTIME_STATUS_MAP = {
    "requested": "requested",
    "manager_approved": "manager_approved",
    "hr_verified": "hr_verified",
    "included_in_payroll": "hr_verified",
    "rejected": "rejected",
    "withdrawn": "withdrawn",
}

APPROVED_LEAVE_STATUS = "approved"
UNKNOWN_LEAVE_TYPE_CODE = "unknown"

_OPS = frozenset({"c", "u", "d", "r", "t"})
_EPOCH_DAY = date(1970, 1, 1)
#: Debezium epoch-millisecond ceiling: 9999-12-31T23:59:59.999Z. Anything
#: outside the representable `datetime` range is a malformed message, not a
#: reason to crash the worker.
_MAX_EPOCH_MILLIS = 253402300799999
#: `fact_leave.approved_day_count` is `UInt32` (database/clickhouse/schema.sql).
_MAX_APPROVED_DAY_COUNT = 4_294_967_295


class CdcMappingError(ValueError):
    """A captured change that cannot be mapped.

    The message names the missing/invalid field only — never its value
    (`.ai/security.md`).
    """


class UnknownLeaveTypeError(CdcMappingError):
    """A `leave_requests` row references a type the sink has not seen yet."""


@dataclass(frozen=True, slots=True)
class Change:
    """One parsed Debezium change envelope."""

    op: str
    table: str
    commit_ts: datetime
    snapshot: str | None
    after: Mapping[str, Any] | None
    before: Mapping[str, Any] | None

    @property
    def dropped(self) -> bool:
        """Delete/truncate ops never remove a projection row (rule 8)."""
        return self.op in ("d", "t")

    @property
    def snapshot_done(self) -> bool:
        """Snapshot-complete evidence for the readiness fence (D5)."""
        return self.snapshot in (None, "false", "last")


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """A Debezium heartbeat — no row, but snapshot-complete evidence (D5)."""

    commit_ts: datetime


@dataclass(frozen=True, slots=True)
class LeaveType:
    """One captured `leave_types` reference row."""

    tenant_id: UUID
    leave_type_id: UUID
    code: str


class LeaveTypeBook:
    """In-memory leave-type dictionary, warmed by the capture stream (D7).

    Keyed by `(tenant_id, leave_type_id)` so a reference lookup can never cross
    tenants even if an id were duplicated. The consumer applies every
    `leave_types` change; the sink's main process replays the topic on restart
    so a fresh dictionary is warm before requests arrive.
    """

    def __init__(self) -> None:
        self._codes: dict[tuple[UUID, UUID], str] = {}

    def __len__(self) -> int:
        return len(self._codes)

    def apply(self, leave_type: LeaveType) -> None:
        self._codes[(leave_type.tenant_id, leave_type.leave_type_id)] = leave_type.code

    def resolve(self, tenant_id: UUID, leave_type_id: UUID) -> str | None:
        return self._codes.get((tenant_id, leave_type_id))


# --- Envelope parsing -----------------------------------------------------


def parse_message(value: bytes | None) -> Change | Heartbeat | None:
    """Decode one topic message.

    Returns `None` for anything that is not a Debezium change/heartbeat (the
    consumer skips it, ids-only). A structurally valid change with a captured
    table is decoded strictly; a bad field raises `CdcMappingError`.
    """
    try:
        payload = json.loads(value or b"")
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(payload, Mapping):
        return None

    source = payload.get("source")
    if source is None:
        # Heartbeat values are `{"ts_ms": ...}` with no source/op (Debezium).
        ts_ms = payload.get("ts_ms")
        if isinstance(ts_ms, int) and not isinstance(ts_ms, bool):
            return Heartbeat(commit_ts=_from_epoch_millis(ts_ms))
        return None
    if not isinstance(source, Mapping):
        raise CdcMappingError("source is not an object")

    table = source.get("table")
    if not isinstance(table, str) or table not in CAPTURED_TABLES:
        return None

    op = payload.get("op")
    if not isinstance(op, str) or op not in _OPS:
        raise CdcMappingError("op is missing")

    ts_ms = source.get("ts_ms")
    if not isinstance(ts_ms, int) or isinstance(ts_ms, bool):
        raise CdcMappingError("source.ts_ms is missing")

    snapshot = _snapshot(source.get("snapshot"))
    after = _row_image(payload.get("after"), "after")
    before = _row_image(payload.get("before"), "before")
    if op in ("c", "r") and after is None:
        raise CdcMappingError("after is missing")
    # `u` and `d` need only the post-image for the sink's upsert; Debezium may
    # emit a null `before` (or a placeholder-filled one) when `column.include`
    # filtering removes the replica-identity columns. Delete/truncate are
    # dropped before mapping, so requiring `before` would only misclassify a
    # real change as malformed.
    if op == "u" and after is None:
        raise CdcMappingError("after is missing")

    return Change(
        op=op,
        table=table,
        commit_ts=_from_epoch_millis(ts_ms),
        snapshot=snapshot,
        after=after,
        before=before,
    )


def _snapshot(value: object) -> str | None:
    if value is None:
        return None
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str) and value in (
        "true",
        "false",
        "last",
        # Debezium 3.x initial/incremental snapshot markers. `first` and
        # `last_in_data_collection` are still snapshot rows and must not flip
        # readiness; only `last`/streaming/heartbeat are completion evidence.
        "first",
        "first_in_data_collection",
        "last_in_data_collection",
    ):
        return value
    raise CdcMappingError("source.snapshot is invalid")


def _row_image(value: object, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CdcMappingError(f"{field} is not an object")
    return value


def _from_epoch_millis(value: int) -> datetime:
    if not 0 <= value <= _MAX_EPOCH_MILLIS:
        raise CdcMappingError("ts_ms is out of range")
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise CdcMappingError("ts_ms is not a date") from exc


# --- Row decoding ---------------------------------------------------------


def decode_employee(after: Mapping[str, Any]) -> EmployeeRow:
    return EmployeeRow(
        tenant_id=_uuid(after, "tenant_id"),
        employee_id=_uuid(after, "id"),
        hired_at=_date(after, "hired_at"),
        terminated_at=_optional_date(after, "terminated_at"),
    )


def decode_assignment(after: Mapping[str, Any]) -> AssignmentRow:
    return AssignmentRow(
        tenant_id=_uuid(after, "tenant_id"),
        employee_id=_uuid(after, "employee_id"),
        effective_from=_date(after, "effective_from"),
        effective_to=_optional_date(after, "effective_to"),
        department_id=_uuid(after, "department_id"),
        position_id=_uuid(after, "position_id"),
        location_id=_uuid(after, "location_id"),
    )


def decode_attendance(after: Mapping[str, Any]) -> AttendanceFact:
    # Dimension stamps stay null on the CDC path: attribution is resolved at
    # query time from captured `dim_assignment` intervals (D3).
    return AttendanceFact(
        tenant_id=_uuid(after, "tenant_id"),
        attendance_id=_uuid(after, "id"),
        employee_id=_uuid(after, "employee_id"),
        workday=_date(after, "workday"),
        duration_minutes=_optional_int(after, "duration_minutes"),
        late=_bool(after, "late"),
        minutes_late=_optional_int(after, "minutes_late"),
    )


def decode_overtime(after: Mapping[str, Any]) -> OvertimeFact:
    status = _text(after, "status")
    projected = _OVERTIME_STATUS_MAP.get(status)
    if projected is None:
        raise CdcMappingError("status is not an overtime status")
    return OvertimeFact(
        tenant_id=_uuid(after, "tenant_id"),
        overtime_id=_uuid(after, "id"),
        employee_id=_uuid(after, "employee_id"),
        work_date=_date(after, "work_date"),
        hours=_decimal(after, "hours"),
        status=projected,
    )


def decode_leave(
    after: Mapping[str, Any],
    leave_types: LeaveTypeBook,
    *,
    allow_unknown_type: bool = False,
) -> LeaveFact | None:
    """One `fact_leave` row for an approved request; `None` for other states.

    `approved` is terminal, so a non-approved image is simply not a fact — it
    is not a delete and never removes a previously projected row.
    """
    if _text(after, "status") != APPROVED_LEAVE_STATUS:
        return None
    tenant_id = _uuid(after, "tenant_id")
    leave_type_id = _uuid(after, "leave_type_id")
    code = leave_types.resolve(tenant_id, leave_type_id)
    if code is None:
        if not allow_unknown_type:
            raise UnknownLeaveTypeError("leave type is not in the dictionary")
        code = UNKNOWN_LEAVE_TYPE_CODE
    days = _int(after, "days")
    if not 1 <= days <= _MAX_APPROVED_DAY_COUNT:
        raise CdcMappingError("days is out of the projection range")
    return LeaveFact(
        tenant_id=tenant_id,
        leave_request_id=_uuid(after, "id"),
        employee_id=_uuid(after, "employee_id"),
        start_date=_date(after, "start_date"),
        leave_type_code=code,
        approved_day_count=days,
    )


def decode_leave_type(after: Mapping[str, Any]) -> LeaveType:
    return LeaveType(
        tenant_id=_uuid(after, "tenant_id"),
        leave_type_id=_uuid(after, "id"),
        code=_text(after, "code"),
    )


# --- Wire decoding --------------------------------------------------------


def _uuid(row: Mapping[str, Any], field: str) -> UUID:
    value = _text(row, field)
    try:
        return UUID(value)
    except ValueError as exc:
        raise CdcMappingError(f"{field} is not a UUID") from exc


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise CdcMappingError(f"{field} is missing")
    return value


def _int(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CdcMappingError(f"{field} is not an integer")
    return value


def _optional_int(row: Mapping[str, Any], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    return _int(row, field)


def _bool(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise CdcMappingError(f"{field} is not a boolean")
    return value


def _date(row: Mapping[str, Any], field: str) -> date:
    days = _int(row, field)
    try:
        return _EPOCH_DAY + timedelta(days=days)
    except OverflowError as exc:
        raise CdcMappingError(f"{field} is not a date") from exc


def _optional_date(row: Mapping[str, Any], field: str) -> date | None:
    if row.get(field) is None:
        return None
    return _date(row, field)


def _decimal(row: Mapping[str, Any], field: str) -> Decimal:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise CdcMappingError(f"{field} is not a decimal string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise CdcMappingError(f"{field} is not a decimal string") from exc


__all__ = [
    "APPROVED_LEAVE_STATUS",
    "CAPTURED_TABLES",
    "CDC_TOPIC_PREFIX",
    "DATA_TOPICS",
    "HEARTBEAT_TOPIC",
    "LEAVE_TYPE_TOPIC",
    "UNKNOWN_LEAVE_TYPE_CODE",
    "Change",
    "CdcMappingError",
    "Heartbeat",
    "LeaveType",
    "LeaveTypeBook",
    "UnknownLeaveTypeError",
    "decode_assignment",
    "decode_attendance",
    "decode_employee",
    "decode_leave",
    "decode_leave_type",
    "decode_overtime",
    "parse_message",
]
