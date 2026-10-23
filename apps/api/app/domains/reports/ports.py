"""Read-only ports the reports read side depends on (T5, ADR 0019).

Both are display/lookup seams, never projection inputs (the notifications
`RecipientDirectory` pattern, ADR 0015):

- `ReferenceLabels` resolves a group key to its display name at query time.
  ClickHouse rows carry identifiers only — never names (`.ai/security.md`).
  The adapter may read the Organizations/Leave reference tables directly but
  must never write them.
- `EmployeeScope` resolves a manager's self + direct-reports set — the exact
  same query that gates the leave/overtime inboxes, so report scope and inbox
  scope can never disagree.

Adapters live in `app.infrastructure.postgres`; the domain never imports them.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID


class ReferenceLabels(Protocol):
    """Read-only display-name lookup for report group keys (per tenant)."""

    async def labels(
        self, tenant_id: UUID, dimension: str, keys: Sequence[str]
    ) -> Mapping[str, str]:
        """Map each known key to its display label; unknown keys are omitted.

        `dimension` is one of `department|location|position|leave_type`.
        Seniority bands are a closed local vocabulary and never reach this
        port.
        """
        ...


class EmployeeScope(Protocol):
    """Read-only manager scope: self + direct reports (one level)."""

    async def visible_employee_ids(
        self, tenant_id: UUID, manager_employee_id: UUID | None
    ) -> Sequence[UUID]:
        """The manager's visible employee ids; `[]` for an unlinked manager."""
        ...
