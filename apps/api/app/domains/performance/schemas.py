"""Wire contracts for the Performance domain (T2).

The API contract is pinned by `.ai/features/performance.md` (## API contract).
Every wire type lands here in T2 even though routes arrive in T5–T7: one
vocabulary, one place to check against the pinned JSON.

Statuses are lowercase `StrEnum`s end to end. Writes reject unknown fields
(`extra="forbid"`): no client-supplied id, status or timestamp. Field bounds
are schema `422`s; state rules live in the service.

PII (`.ai/security.md`): goal `title`/`description`, review
`period`/`strengths`/`improvements`, feedback `comment` and employee-skill
`note` are returned to viewers with row visibility but must never be logged,
emitted in events or written into audit `changes` values. Ratings are
evaluative values and never appear in events either.

The provider shell: `Review` keeps `period`/`rating`/`strengths`/
`improvements`/`feedback_provider_ids` nullable so a nominated provider with no
other standing can see the review shell needed to file feedback without the
draft's evaluative text. The subject-facing serialization omits
`feedback_provider_ids` entirely (service serialization, T6).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.pagination import Page


class GoalStatus(StrEnum):
    """Pinned goal lifecycle (`draft → active → completed | cancelled`)."""

    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReviewStatus(StrEnum):
    """Pinned review lifecycle (`draft → completed | cancelled`)."""

    DRAFT = "draft"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Goal(BaseModel):
    """One goal and its state (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    # PII — visibility is enforced by the service; never logged or emitted.
    title: str
    description: str | None = None
    # Past due dates are allowed: goals are plans, not claims.
    due_date: date | None = None
    progress_pct: int
    status: GoalStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class GoalCreate(BaseModel):
    """Create a goal — always `draft`; the subject defaults to the actor."""

    model_config = ConfigDict(extra="forbid")

    employee_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    due_date: date | None = None
    progress_pct: int = Field(default=0, ge=0, le=100)


class GoalUpdate(BaseModel):
    """Edit a `draft`/`active` goal; absent fields are unchanged.

    Progress is a field update, not a state transition. Explicit `null` clears
    the optional `description`/`due_date` but is rejected for `title` and
    `progress_pct`.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    due_date: date | None = None
    progress_pct: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("title", "progress_pct"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class Review(BaseModel):
    """One review and its state (pinned response shape).

    The provider shell leaves every evaluative field `None`; the subject-facing
    serialization omits `feedback_provider_ids` (T6).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    # PII — never logged, never in events, never in audit `changes` values.
    period: str | None = None
    rating: int | None = None
    strengths: str | None = None
    improvements: str | None = None
    status: ReviewStatus
    feedback_provider_ids: list[UUID] | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ReviewCreate(BaseModel):
    """Start a draft review for an active employee other than the actor."""

    model_config = ConfigDict(extra="forbid")

    employee_id: UUID
    period: str | None = Field(default=None, min_length=1, max_length=50)
    feedback_provider_ids: list[UUID] = Field(default_factory=list, max_length=10)


class ReviewUpdate(BaseModel):
    """Edit a draft review; absent fields are unchanged.

    `feedback_provider_ids` may be cleared with `[]` but not `null` (omitting it
    means unchanged).
    """

    model_config = ConfigDict(extra="forbid")

    period: str | None = Field(default=None, min_length=1, max_length=50)
    rating: int | None = Field(default=None, ge=1, le=5)
    strengths: str | None = Field(default=None, min_length=1, max_length=2000)
    improvements: str | None = Field(default=None, min_length=1, max_length=2000)
    feedback_provider_ids: list[UUID] | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def _reject_explicit_null_providers(self) -> Self:
        if (
            "feedback_provider_ids" in self.model_fields_set
            and self.feedback_provider_ids is None
        ):
            raise ValueError("feedback_provider_ids must not be null")
        return self


class ReviewFeedbackCreate(BaseModel):
    """A nominated provider's one feedback comment on a draft review."""

    model_config = ConfigDict(extra="forbid")

    # PII — never logged, echoed in errors or written to audit `changes`.
    comment: str = Field(min_length=1, max_length=2000)


class ReviewFeedback(BaseModel):
    """One immutable feedback comment (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    author_id: UUID
    comment: str
    created_at: datetime


class ReviewFeedbackList(BaseModel):
    """The pinned `GET …/reviews/{id}/feedback` envelope."""

    items: list[ReviewFeedback]


class Skill(BaseModel):
    """One catalog skill (pinned response shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    # PII — never logged, never in events, never in audit `changes` values.
    description: str | None = None
    archived_at: datetime | None = None


class SkillCreate(BaseModel):
    """Add a skill to the tenant catalog."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=500)


class SkillUpdate(BaseModel):
    """Rename or re-describe a catalog skill; absent fields are unchanged."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _reject_explicit_null_name(self) -> Self:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name must not be null")
        return self


class EmployeeSkill(BaseModel):
    """One employee's proficiency in one catalog skill (pinned response)."""

    model_config = ConfigDict(from_attributes=True)

    skill_id: UUID
    skill_name: str
    proficiency: int
    # PII — never logged, never in events, never in audit `changes` values.
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class EmployeeSkillCreate(BaseModel):
    """Attach an active catalog skill to an employee."""

    model_config = ConfigDict(extra="forbid")

    skill_id: UUID
    proficiency: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, min_length=1, max_length=500)


class EmployeeSkillUpdate(BaseModel):
    """Replace the proficiency/note on an existing employee-skill row."""

    model_config = ConfigDict(extra="forbid")

    proficiency: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, min_length=1, max_length=500)


class GoalListPage(BaseModel):
    items: list[Goal]
    page: Page


class ReviewListPage(BaseModel):
    items: list[Review]
    page: Page


class SkillListPage(BaseModel):
    items: list[Skill]
    page: Page
