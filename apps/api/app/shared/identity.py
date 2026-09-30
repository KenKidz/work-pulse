"""The request identity consumed by routers and domains (ADR 0004).

Phase 2: the identity is resolved from the `wp_session` cookie by the
`get_identity` dependency in `app/dependencies.py` (the composition root) —
the dev-header parsing this module used to own is deleted, not flagged
(`.ai/features/identity-access.md`, decision 5). This module keeps the types so
domain imports stay acyclic:

- `Role` — the six pinned roles (`.ai/security.md`), unchanged.
- `Identity` — `{tenant_id, actor_id /* user id */, role, employee_id}`.
  Consumers treat `actor_id` as the authenticated **user** id and resolve
  employee-scoped facts through `employee_id` (`UUID | None`).
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class Role(StrEnum):
    """The six pinned Phase 1 roles (`.ai/security.md`)."""

    EMPLOYEE = "employee"
    MANAGER = "manager"
    HR = "hr"
    FINANCE = "finance"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class Identity(BaseModel):
    """Validated identity of the current request, resolved from the session."""

    tenant_id: UUID
    actor_id: UUID  # the authenticated user id
    role: Role
    employee_id: UUID | None = None  # the user's optional employee link
