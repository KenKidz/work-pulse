"""Postgres repositories for the Employees domain.

Every statement carries `WHERE tenant_id = :tenant` — tenant scoping lives
here and nowhere else (`.ai/security.md`). Unique-violation `IntegrityError`s
are mapped to the domain `*_taken` errors (the DB constraint is the race
backstop behind the service pre-check); constraint names are the explicit ones
declared in `domains/employees/models.py`.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees import models as orm
from app.domains.employees.exceptions import (
    EmployeeCodeTakenError,
    EmployeeEmailTakenError,
)
from app.domains.employees.repository import EmployeeFilters
from app.domains.employees.schemas import (
    Employee,
    EmployeeStatus,
    Employment,
    EmploymentType,
)
from app.infrastructure.postgres.like import LIKE_ESCAPE, contains_pattern
from app.shared.errors import WorkPulseError
from app.shared.pagination import PageParams

_CODE_PATTERN = r"^EMP-[0-9]+$"
# The auto-code suffix is bounded to 9 digits: larger HR-supplied numeric
# overrides are ignored by the scan (and cannot overflow the int cast).
_CODE_SUFFIX_MAX_DIGITS = 9


def _map_unique_violation(exc: IntegrityError) -> WorkPulseError | None:
    """Map a unique-constraint race to the pinned domain error.

    Asyncpg exposes the violated constraint/index name; the message fallback
    keeps the mapping working for wrapped drivers. Anything else (e.g. a
    foreign-key violation) is not ours to reinterpret — the caller re-raises it
    unchanged.
    """
    haystack = getattr(exc.orig, "constraint_name", None) or str(exc.orig)
    if "uq_employees_tenant_email_lower" in haystack:
        return EmployeeEmailTakenError()
    if "uq_employees_tenant_employee_code" in haystack:
        return EmployeeCodeTakenError()
    return None


class PostgresEmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tenant_id: UUID, employee_id: UUID) -> Employee | None:
        stmt = select(orm.Employee).where(
            orm.Employee.id == employee_id,
            orm.Employee.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Employee.model_validate(row) if row is not None else None

    async def email_taken(
        self, tenant_id: UUID, email: str, exclude_id: UUID | None = None
    ) -> bool:
        stmt = select(orm.Employee.id).where(
            orm.Employee.tenant_id == tenant_id,
            func.lower(orm.Employee.email) == email.lower(),
        )
        if exclude_id is not None:
            stmt = stmt.where(orm.Employee.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def code_taken(self, tenant_id: UUID, employee_code: str) -> bool:
        stmt = select(orm.Employee.id).where(
            orm.Employee.tenant_id == tenant_id,
            orm.Employee.employee_code == employee_code,
        )
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def next_employee_code(self, tenant_id: UUID) -> str:
        suffix = func.substr(orm.Employee.employee_code, 5)
        stmt = select(func.max(cast(suffix, Integer))).where(
            orm.Employee.tenant_id == tenant_id,
            orm.Employee.employee_code.op("~")(_CODE_PATTERN),
            func.length(suffix) <= _CODE_SUFFIX_MAX_DIGITS,
        )
        max_suffix = (await self._session.execute(stmt)).scalar_one_or_none()
        return f"EMP-{max_suffix + 1:05d}" if max_suffix is not None else "EMP-00001"

    async def list(
        self, tenant_id: UUID, filters: EmployeeFilters, page: PageParams
    ) -> tuple[Sequence[Employee], int]:
        conditions = [orm.Employee.tenant_id == tenant_id]
        if filters.q:
            pattern = contains_pattern(filters.q)
            conditions.append(
                or_(
                    orm.Employee.first_name.ilike(pattern, escape=LIKE_ESCAPE),
                    orm.Employee.last_name.ilike(pattern, escape=LIKE_ESCAPE),
                    orm.Employee.email.ilike(pattern, escape=LIKE_ESCAPE),
                    orm.Employee.employee_code.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
        if filters.department_id is not None:
            conditions.append(orm.Employee.department_id == filters.department_id)
        if filters.position_id is not None:
            conditions.append(orm.Employee.position_id == filters.position_id)
        if filters.location_id is not None:
            conditions.append(orm.Employee.location_id == filters.location_id)
        if filters.status is not None:
            conditions.append(orm.Employee.status == filters.status.value)
        if filters.manager_id is not None:
            conditions.append(orm.Employee.manager_id == filters.manager_id)

        total = (
            await self._session.execute(
                select(func.count()).select_from(orm.Employee).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                select(orm.Employee)
                .where(*conditions)
                # Stable pagination (pinned): last name, first name, then the
                # unique id as the tiebreaker so equal names cannot shuffle.
                .order_by(
                    orm.Employee.last_name.asc(),
                    orm.Employee.first_name.asc(),
                    orm.Employee.id.asc(),
                )
                .limit(page.limit)
                .offset(page.offset)
            )
        ).scalars()
        return [Employee.model_validate(row) for row in rows], total

    async def add(
        self,
        tenant_id: UUID,
        employee_id: UUID,
        *,
        employee_code: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
        manager_id: UUID | None,
        employment_type: EmploymentType,
        status: EmployeeStatus,
        hired_at: date,
    ) -> Employee:
        row = orm.Employee(
            id=employee_id,
            tenant_id=tenant_id,
            employee_code=employee_code,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            manager_id=manager_id,
            employment_type=employment_type.value,
            status=status.value,
            hired_at=hired_at,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return Employee.model_validate(row)

    async def update(
        self, tenant_id: UUID, employee_id: UUID, **changes: Any
    ) -> Employee | None:
        stmt = select(orm.Employee).where(
            orm.Employee.id == employee_id,
            orm.Employee.tenant_id == tenant_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # email race after the service pre-check
            mapped = _map_unique_violation(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.refresh(row)
        return Employee.model_validate(row)

    async def direct_reports(
        self, tenant_id: UUID, manager_id: UUID
    ) -> Sequence[Employee]:
        stmt = (
            select(orm.Employee)
            .where(
                orm.Employee.tenant_id == tenant_id,
                orm.Employee.manager_id == manager_id,
            )
            .order_by(
                orm.Employee.last_name.asc(),
                orm.Employee.first_name.asc(),
                orm.Employee.id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [Employee.model_validate(row) for row in rows]

    async def clear_manager(self, tenant_id: UUID, manager_id: UUID) -> Sequence[UUID]:
        stmt = (
            update(orm.Employee)
            .where(
                orm.Employee.tenant_id == tenant_id,
                orm.Employee.manager_id == manager_id,
            )
            .values(manager_id=None)
            .returning(orm.Employee.id)
        )
        return list((await self._session.execute(stmt)).scalars().all())


class PostgresEmploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self, tenant_id: UUID, employee_id: UUID) -> Employment | None:
        stmt = (
            select(orm.Employment)
            .where(
                orm.Employment.tenant_id == tenant_id,
                orm.Employment.employee_id == employee_id,
                orm.Employment.effective_to.is_(None),
            )
            .order_by(orm.Employment.effective_from.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Employment.model_validate(row) if row is not None else None

    async def history(self, tenant_id: UUID, employee_id: UUID) -> Sequence[Employment]:
        stmt = (
            select(orm.Employment)
            .where(
                orm.Employment.tenant_id == tenant_id,
                orm.Employment.employee_id == employee_id,
            )
            .order_by(orm.Employment.effective_from.desc())
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [Employment.model_validate(row) for row in rows]

    async def add(
        self,
        tenant_id: UUID,
        employment_id: UUID,
        *,
        employee_id: UUID,
        department_id: UUID,
        position_id: UUID,
        location_id: UUID,
        employment_type: EmploymentType,
        effective_from: date,
        effective_to: date | None = None,
    ) -> Employment:
        row = orm.Employment(
            id=employment_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            employment_type=employment_type.value,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return Employment.model_validate(row)

    async def close(
        self, tenant_id: UUID, employment_id: UUID, effective_to: date
    ) -> Employment | None:
        stmt = (
            update(orm.Employment)
            .where(
                orm.Employment.id == employment_id,
                orm.Employment.tenant_id == tenant_id,
                # Only an OPEN period may be closed: a concurrent close must
                # not be overwritten (returns None → caller re-raises).
                orm.Employment.effective_to.is_(None),
            )
            .values(effective_to=effective_to)
            .returning(orm.Employment)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return Employment.model_validate(row) if row is not None else None
