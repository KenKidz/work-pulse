"""In-memory fakes for the Employees domain unit tests.

No I/O anywhere (testing.md rule 4 — these tests pass with postgres-test
stopped). Fakes honor tenant scope, the pinned filters, deterministic
timestamps, and record every audit entry. Uniqueness mirrors the DB backstop:
a duplicate email/code raises the same domain error the Postgres adapter maps
from `IntegrityError`.
"""

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

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
from app.shared.pagination import PageParams

CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


class FakeEmployeeRepo:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID], Employee] = {}

    async def get(self, tenant_id: UUID, employee_id: UUID) -> Employee | None:
        return self.rows.get((tenant_id, employee_id))

    async def email_taken(
        self, tenant_id: UUID, email: str, exclude_id: UUID | None = None
    ) -> bool:
        return any(
            row.email.lower() == email.lower() and row.id != exclude_id
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        )

    async def code_taken(self, tenant_id: UUID, employee_code: str) -> bool:
        return any(
            row.employee_code == employee_code
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
        )

    async def next_employee_code(self, tenant_id: UUID) -> str:
        suffixes = [
            int(row.employee_code.removeprefix("EMP-"))
            for (row_tenant, _), row in self.rows.items()
            if row_tenant == tenant_id
            and re.fullmatch(r"EMP-\d+", row.employee_code) is not None
        ]
        return f"EMP-{max(suffixes, default=0) + 1:05d}"

    async def list(
        self, tenant_id: UUID, filters: EmployeeFilters, page: PageParams
    ) -> tuple[Sequence[Employee], int]:
        rows = [
            row for (row_tenant, _), row in self.rows.items() if row_tenant == tenant_id
        ]
        if filters.q:
            needle = filters.q.lower()

            def matches(row: Employee) -> bool:
                return any(
                    needle in value.lower()
                    for value in (
                        row.first_name,
                        row.last_name,
                        row.email,
                        row.employee_code,
                    )
                )

            rows = [row for row in rows if matches(row)]
        if filters.department_id is not None:
            rows = [row for row in rows if row.department_id == filters.department_id]
        if filters.position_id is not None:
            rows = [row for row in rows if row.position_id == filters.position_id]
        if filters.location_id is not None:
            rows = [row for row in rows if row.location_id == filters.location_id]
        if filters.status is not None:
            rows = [row for row in rows if row.status == filters.status]
        if filters.manager_id is not None:
            rows = [row for row in rows if row.manager_id == filters.manager_id]
        rows.sort(key=lambda row: (row.last_name, row.first_name, row.id))
        total = len(rows)
        return rows[page.offset : page.offset + page.limit], total

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
        if await self.email_taken(tenant_id, email):  # DB unique-constraint backstop
            raise EmployeeEmailTakenError()
        if await self.code_taken(tenant_id, employee_code):
            raise EmployeeCodeTakenError()
        row = Employee(
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
            employment_type=employment_type,
            status=status,
            hired_at=hired_at,
            terminated_at=None,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        self.rows[(tenant_id, employee_id)] = row
        return row

    async def update(
        self, tenant_id: UUID, employee_id: UUID, **changes: Any
    ) -> Employee | None:
        key = (tenant_id, employee_id)
        row = self.rows.get(key)
        if row is None:
            return None
        if "email" in changes and await self.email_taken(
            tenant_id, changes["email"], exclude_id=employee_id
        ):
            raise EmployeeEmailTakenError()
        updated = row.model_copy(update={**changes, "updated_at": CREATED_AT})
        self.rows[key] = updated
        return updated

    async def direct_reports(
        self, tenant_id: UUID, manager_id: UUID
    ) -> Sequence[Employee]:
        return sorted(
            (
                row
                for (row_tenant, _), row in self.rows.items()
                if row_tenant == tenant_id and row.manager_id == manager_id
            ),
            key=lambda row: (row.last_name, row.first_name, row.id),
        )

    async def clear_manager(self, tenant_id: UUID, manager_id: UUID) -> Sequence[UUID]:
        cleared: list[UUID] = []
        for key, row in list(self.rows.items()):
            if key[0] == tenant_id and row.manager_id == manager_id:
                self.rows[key] = row.model_copy(
                    update={"manager_id": None, "updated_at": CREATED_AT}
                )
                cleared.append(row.id)
        return cleared


class FakeEmploymentRepo:
    def __init__(self) -> None:
        self.periods: list[Employment] = []

    async def current(self, tenant_id: UUID, employee_id: UUID) -> Employment | None:
        open_periods = [
            period
            for period in self.periods
            if period.tenant_id == tenant_id
            and period.employee_id == employee_id
            and period.effective_to is None
        ]
        if not open_periods:
            return None
        return max(open_periods, key=lambda period: period.effective_from)

    async def history(self, tenant_id: UUID, employee_id: UUID) -> Sequence[Employment]:
        matches = [
            period
            for period in self.periods
            if period.tenant_id == tenant_id and period.employee_id == employee_id
        ]
        matches.sort(key=lambda period: period.effective_from, reverse=True)
        return matches

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
        if effective_to is None and any(
            period.tenant_id == tenant_id
            and period.employee_id == employee_id
            and period.effective_to is None
            for period in self.periods
        ):
            # Partial unique index backstop: at most one open period.
            raise AssertionError(
                "unique violation: employee already has an open period"
            )
        period = Employment(
            id=employment_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            department_id=department_id,
            position_id=position_id,
            location_id=location_id,
            employment_type=employment_type,
            effective_from=effective_from,
            effective_to=effective_to,
            created_at=CREATED_AT,
        )
        self.periods.append(period)
        return period

    async def close(
        self, tenant_id: UUID, employment_id: UUID, effective_to: date
    ) -> Employment | None:
        for index, period in enumerate(self.periods):
            if period.tenant_id == tenant_id and period.id == employment_id:
                if period.effective_to is not None:
                    return None  # already closed — never rewrite
                closed = period.model_copy(update={"effective_to": effective_to})
                self.periods[index] = closed
                return closed
        return None
