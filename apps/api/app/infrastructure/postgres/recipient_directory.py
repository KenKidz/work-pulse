"""Read-only recipient directory over Postgres (T3, ADR 0015 decision 1/8).

Events carry no PII, so the worker resolves display names, the direct manager
and the linked account address from reference data through the
`RecipientDirectory` port. This adapter implements it over the ORM models of
the Employees and Identity domains — the documented infrastructure seam
(`PostgresEmployeeLink` is the precedent): reads only, never mutates another
context's tables, and every query is tenant-scoped.

Addressing rules (pinned by the spec):

- Display name is `employees.first_name` + `last_name`.
- The account address is `users.email` (the account), never
  `employees.email`.
- "Team manager" is the employee's `manager_id` (the same source leave's
  decide scope uses).
- A deciding actor is a **user** id (ADR 0010); its display name resolves only
  when the user is linked to an employee. Non-employee accounts (admin/HR)
  resolve to `None` and templates omit the clause (spec R3).
- Identity forbids two accounts linked to one employee at the service level but
  the database does not (spec R6): the directory picks the lowest user id
  deterministically and logs ids only.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as employee_orm
from app.domains.identity import models as user_orm
from app.domains.notifications.schemas import Recipient, SubjectFacts
from app.shared.logging import get_logger

_log = get_logger("notifications.directory")


def _display_name(first_name: str, last_name: str) -> str:
    """The rendered display name — the one personal value templates may use."""
    return f"{first_name} {last_name}".strip()


class PostgresRecipientDirectory:
    """`RecipientDirectory` over `employees` + `users` (read-only, per tenant)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def subject_facts(
        self, tenant_id: UUID, employee_id: UUID
    ) -> SubjectFacts | None:
        stmt = select(
            employee_orm.Employee.first_name,
            employee_orm.Employee.last_name,
            employee_orm.Employee.manager_id,
        ).where(
            employee_orm.Employee.tenant_id == tenant_id,
            employee_orm.Employee.id == employee_id,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return SubjectFacts(
            display_name=_display_name(row.first_name, row.last_name),
            manager_id=row.manager_id,
        )

    async def recipient(self, tenant_id: UUID, employee_id: UUID) -> Recipient | None:
        stmt = (
            select(
                user_orm.User.id,
                user_orm.User.email,
                employee_orm.Employee.first_name,
                employee_orm.Employee.last_name,
            )
            .join(
                employee_orm.Employee,
                employee_orm.Employee.id == user_orm.User.employee_id,
            )
            .where(
                user_orm.User.tenant_id == tenant_id,
                user_orm.User.employee_id == employee_id,
                employee_orm.Employee.tenant_id == tenant_id,
            )
            .order_by(user_orm.User.id.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            # No linked account: person-addressed delivery is impossible and
            # the consumer skips the message (never "send to the channel
            # anyway").
            return None
        if len(rows) > 1:
            # Identity's `employee_link_taken` rule should prevent this; the
            # deterministic pick keeps the worker stable if it ever happens.
            _log.warning(
                "recipient_link_ambiguous",
                tenant_id=str(tenant_id),
                employee_id=str(employee_id),
                linked_accounts=len(rows),
            )
        row = rows[0]
        return Recipient(
            employee_id=employee_id,
            user_id=row.id,
            display_name=_display_name(row.first_name, row.last_name),
            account_address=row.email,
        )

    async def actor_display_name(self, tenant_id: UUID, actor_id: UUID) -> str | None:
        stmt = (
            select(employee_orm.Employee.first_name, employee_orm.Employee.last_name)
            .join(
                user_orm.User,
                user_orm.User.employee_id == employee_orm.Employee.id,
            )
            .where(
                user_orm.User.tenant_id == tenant_id,
                user_orm.User.id == actor_id,
                employee_orm.Employee.tenant_id == tenant_id,
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return _display_name(row.first_name, row.last_name)
