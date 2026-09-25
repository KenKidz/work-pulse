"""Wire contracts for the Organizations domain.

Read schemas are titled exactly `Department`, `Position`, `Location` (pinned —
they drive the generated TypeScript types). PATCH semantics: absent field =
unchanged (`exclude_unset`); explicit `null` for `parent_department_id` moves a
department to the root; explicit `null` for `description`/`address` clears the
field; explicit `null` for a required field is a validation error.

Timezone validity is checked in the service (uniform `invalid_timezone`
error), deliberately NOT here — a Pydantic check would surface as the generic
`validation_error` instead.
"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.pagination import Page


class Department(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    parent_department_id: UUID | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Position(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    description: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Location(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    address: str | None = None
    timezone: str
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_department_id: UUID | None = None


class PositionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    timezone: str = Field(min_length=1, max_length=100)


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_department_id: UUID | None = None

    @model_validator(mode="after")
    def _reject_explicit_null_name(self) -> Self:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name must not be null")
        return self


class PositionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reject_explicit_null_title(self) -> Self:
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title must not be null")
        return self


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("name", "timezone"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class ArchiveRequest(BaseModel):
    """Archive body — the reason is stored on the audit entry only."""

    reason: str | None = Field(default=None, max_length=500)


class DepartmentListPage(BaseModel):
    items: list[Department]
    page: Page


class PositionListPage(BaseModel):
    items: list[Position]
    page: Page


class LocationListPage(BaseModel):
    items: list[Location]
    page: Page
