"""Wire contracts for the Identity domain.

Read schema is titled exactly `User` (pinned — it drives the generated
TypeScript type). `User` never carries password material or session data.

Semantics worth naming here (pinned in `.ai/features/identity-access.md`):

- `LoginRequest` is deliberately unconstrained beyond "a string": an unknown or
  ill-formed email and a wrong password must all collapse into the same
  `401 invalid_credentials` (no validation signal that distinguishes them).
- New passwords (`CreateUser`, `ChangePasswordRequest`, `ResetPasswordRequest`)
  need at least `MIN_PASSWORD_LENGTH` characters, no complexity rules; shorter
  input is a `validation_error` 422.
- `UpdateUser`: absent = unchanged; explicit `null` for `email`/`role` is a
  validation error; explicit `"employee_id": null` unlinks (PATCH semantics).
"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.identity import Role
from app.shared.pagination import Page

# Pinned password rule: minimum 10 characters, no complexity requirements.
MIN_PASSWORD_LENGTH = 10


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    email: str
    role: Role
    employee_id: UUID | None = None
    active: bool
    created_at: datetime
    updated_at: datetime


class Session(BaseModel):
    """The stored session row — internal only, never returned over HTTP.

    The cookie carries the raw token; only `token_hash` is ever persisted.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class CreateUser(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role: Role
    employee_id: UUID | None = None


class UpdateUser(BaseModel):
    email: str | None = Field(default=None, min_length=3, max_length=320)
    role: Role | None = None
    employee_id: UUID | None = None

    @model_validator(mode="after")
    def _reject_explicit_null_required(self) -> Self:
        for field in ("email", "role"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class UserListPage(BaseModel):
    items: list[User]
    page: Page
