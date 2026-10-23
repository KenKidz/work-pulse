"""Wire contracts for the Recruitment domain (T2).

The API contract is pinned by `.ai/features/recruitment.md` (## API contract).
Every wire type lands here in T2 even though the candidate and interview routes
arrive in T3/T5: one vocabulary, one place to check against the pinned JSON.

Status/source values are lowercase `StrEnum`s end to end. Writes reject unknown
fields (`extra="forbid"`): no client-supplied status, id or timestamp. Field
bounds (name/email lengths, `openings < 1`, `rating` out of 1..5, note lengths)
are schema `422`s; state rules live in the service.

PII (`.ai/security.md`): candidate names, email, phone, notes, `stage_note`,
interview `notes`, `feedback_notes` and rejection reasons are returned to
viewers with row visibility but must never be logged, emitted in events or
written into audit `changes` values.

`CandidateDetail` adds the candidate's interviews for management viewers; T5
fills the list (scoped to the caller for an assigned interviewer).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.domains.employees.schemas import EmploymentType
from app.shared.pagination import Page


class JobStatus(StrEnum):
    """Pinned job lifecycle (`draft → open → closed`)."""

    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


class CandidateSource(StrEnum):
    """How the candidate reached the pipeline (intake is HR-entered)."""

    REFERRAL = "referral"
    JOB_BOARD = "job_board"
    AGENCY = "agency"
    OTHER = "other"


class CandidateStatus(StrEnum):
    """Pinned candidate pipeline and its terminal side exits."""

    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFERED = "offered"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class CandidateRejectedFrom(StrEnum):
    """The observed pre-rejection state recorded on rejected rows."""

    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFERED = "offered"


class InterviewStatus(StrEnum):
    """Pinned interview lifecycle (`scheduled → completed | cancelled`)."""

    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    """One hiring opening (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    description: str | None = None
    department_id: UUID
    position_id: UUID
    location_id: UUID
    openings: int
    status: JobStatus
    created_by: UUID
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class JobCreate(BaseModel):
    """Create a job — always `draft` (opening is its own action)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    department_id: UUID
    position_id: UUID
    location_id: UUID
    openings: int = Field(default=1, ge=1)


class JobUpdate(BaseModel):
    """Edit a job while `draft`/`open`; absent fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    department_id: UUID | None = None
    position_id: UUID | None = None
    location_id: UUID | None = None
    openings: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in (
            "title",
            "department_id",
            "position_id",
            "location_id",
            "openings",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class Candidate(BaseModel):
    """One application and its pipeline state (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    job_id: UUID
    # PII — visibility is enforced by the service; never logged or emitted.
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    source: CandidateSource
    notes: str | None = None
    status: CandidateStatus
    stage_note: str | None = None
    rejected_from: CandidateRejectedFrom | None = None
    hired_at: datetime | None = None
    employee_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class CandidateCreate(BaseModel):
    """HR-entered intake on an open job (no public application surface)."""

    model_config = ConfigDict(extra="forbid")

    # PII fields: bounded here, never logged or echoed in errors.
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    source: CandidateSource
    notes: str | None = Field(default=None, min_length=1, max_length=2000)


class CandidateUpdate(BaseModel):
    """Edit contact fields; absent fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=200)
    last_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    source: CandidateSource | None = None
    notes: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("first_name", "last_name", "email", "source"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class CandidateDecision(BaseModel):
    """Optional reject/withdraw or cancel note — 1..500 characters (PII)."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, min_length=1, max_length=500)


class HireCandidate(BaseModel):
    """Hire terms; names/email/phone override the candidate's record.

    `hired_at` in the future is a service `422` (the validator cannot read the
    injected clock — risk 6 in the spec).
    """

    model_config = ConfigDict(extra="forbid")

    employment_type: EmploymentType
    hired_at: date
    manager_id: UUID | None = None
    employee_code: str | None = Field(default=None, min_length=1, max_length=50)
    first_name: str | None = Field(default=None, min_length=1, max_length=200)
    last_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=50)


class Interview(BaseModel):
    """One scheduled conversation (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    candidate_id: UUID
    job_id: UUID
    interviewer_id: UUID
    scheduled_at: datetime
    # PII — never logged, never in events, never in audit `changes` values.
    notes: str | None = None
    status: InterviewStatus
    rating: int | None = None
    feedback_notes: str | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InterviewSchedule(BaseModel):
    """Schedule on an `interviewing` candidate for an active employee."""

    model_config = ConfigDict(extra="forbid")

    interviewer_id: UUID
    scheduled_at: AwareDatetime
    notes: str | None = Field(default=None, min_length=1, max_length=1000)


class InterviewUpdate(BaseModel):
    """Reschedule / re-assign / re-note while `scheduled`."""

    model_config = ConfigDict(extra="forbid")

    interviewer_id: UUID | None = None
    scheduled_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("interviewer_id", "scheduled_at"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class InterviewFeedback(BaseModel):
    """The assigned interviewer's completion feedback — `interview.completed`."""

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    notes: str | None = Field(default=None, min_length=1, max_length=1000)


class CandidateDetail(Candidate):
    """Candidate plus its interviews (management: all; interviewer: own)."""

    interviews: list[Interview] = Field(default_factory=list)


class JobListPage(BaseModel):
    items: list[Job]
    page: Page


class CandidateListPage(BaseModel):
    items: list[Candidate]
    page: Page


class InterviewListPage(BaseModel):
    items: list[Interview]
    page: Page
