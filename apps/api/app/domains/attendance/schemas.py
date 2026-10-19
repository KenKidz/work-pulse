"""Wire contracts for the Attendance domain.

Read schemas are titled exactly `AttendanceRecord`, `Board`, `Shift` (pinned —
they drive the generated TypeScript types).

Semantics worth naming here (pinned in `.ai/features/attendance.md`):

- `workday` is the calendar date of the check-in in the employee's location
  timezone; `checked_in_at`/`checked_out_at`/`created_at`/`updated_at` are
  date-times. `duration_minutes` is `null` while a record is open.
- `Shift.start_time`/`end_time` are `"HH:MM"` strings on the wire (`time`
  in Python, serialized without seconds).
- `CheckInRequest.work_mode` is deliberately a plain string: validation lives
  in the service so an invalid mode yields `400 invalid_work_mode`, not the
  generic `422 validation_error` an enum would produce.
- `CheckInRequest`/`CheckOutRequest` (and the shift writes) reject unknown
  fields (`extra="forbid"`): client-supplied timestamps are a trust problem
  (Decision #3), so they fail loudly instead of being ignored.
- Notes are free text, 1..500 chars, and treated like PII — never logged,
  never in events or audit `changes` values (`.ai/security.md`).
"""

import re
from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from app.shared.pagination import Page

_HH_MM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class WorkMode(StrEnum):
    ONSITE = "onsite"
    REMOTE = "remote"


class BoardAction(StrEnum):
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"


# Live presence vocabulary — one status plus the orthogonal `late` flag
# (ADR 0009). `late` is never a status; `on_leave` is produced for employees
# covered by APPROVED leave (ADR 0014).
PresenceStatus = Literal["present", "remote", "checked_out", "absent", "on_leave"]


def _require_hh_mm(value: Any) -> Any:
    """`"HH:MM"` only on input; pass through when the service passes a `time`."""
    if isinstance(value, str) and _HH_MM.fullmatch(value) is None:
        raise ValueError("must be HH:MM")
    return value


HHMM = Annotated[time, BeforeValidator(_require_hh_mm)]


class AttendanceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    employee_id: UUID
    workday: date
    checked_in_at: datetime
    checked_out_at: datetime | None = None
    work_mode: WorkMode
    late: bool
    shift_id: UUID | None = None
    check_in_note: str | None = None
    check_out_note: str | None = None
    duration_minutes: int | None = None  # null while open; floor minutes at close
    created_at: datetime


class BoardCounts(BaseModel):
    """Independent counts — not a partition (a person can be present and late)."""

    present: int
    remote: int
    late: int
    on_leave: int  # APPROVED-leave coverage for the date (ADR 0014)
    absent: int
    checked_out: int


class BoardActivity(BaseModel):
    occurred_at: datetime
    employee_id: UUID
    action: BoardAction


class Board(BaseModel):
    date: date
    counts: BoardCounts
    activity: list[BoardActivity]


class BoardPresenceRow(BaseModel):
    """One employee's presence for the workday — the shared projection row.

    The realtime snapshot and deltas project onto this shape; identifiers and
    state only, never names/notes (`.ai/security.md`). `late` is orthogonal to
    `status`; `minutes_late` is recomputed from the record's shift at read time
    (the stored flag itself is immutable), and `since` is `checked_in_at` while
    the record is open / `checked_out_at` once closed / `null` for
    absent/on_leave rows.
    """

    employee_id: UUID
    status: PresenceStatus
    late: bool
    minutes_late: int | None = None
    since: datetime | None = None
    attendance_id: UUID | None = None


class Shift(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    start_time: time
    end_time: time
    grace_minutes: int
    location_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("start_time", "end_time", when_used="json")
    def _serialize_hh_mm(self, value: time) -> str:
        return value.strftime("%H:%M")


class ShiftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    start_time: HHMM
    end_time: HHMM
    grace_minutes: int = Field(ge=0)
    location_id: UUID | None = None


class ShiftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    start_time: HHMM | None = None
    end_time: HHMM | None = None
    grace_minutes: int | None = Field(default=None, ge=0)
    location_id: UUID | None = None  # explicit null = become the tenant default

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("name", "start_time", "end_time", "grace_minutes"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class CheckInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_mode: str  # plain string — the service validates → 400 invalid_work_mode
    check_in_note: str | None = Field(default=None, min_length=1, max_length=500)


class CheckOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_out_note: str | None = Field(default=None, min_length=1, max_length=500)


class AttendanceRecordListPage(BaseModel):
    items: list[AttendanceRecord]
    page: Page


ShiftList = list[Shift]
