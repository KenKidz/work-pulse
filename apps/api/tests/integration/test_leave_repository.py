"""Repository integration tests for Leave (T3).

Real PostgreSQL: tenant scoping in both directions, idempotent seeded types,
lazy balances, inclusive overlap rules, conditional transitions, the race-safe
deduction guard, and paged reads.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.leave.repository import LeaveRequestFilters
from app.domains.leave.schemas import LeaveRequestStatus, LeaveTypeCode
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.leave import PostgresLeaveRepository
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.shared.pagination import PageParams
from tests.integration.conftest import TENANT_A, TENANT_B

HIRED_AT = date(2026, 1, 5)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class OrgRefs:
    """Deterministic organization rows to reference from employees."""

    def __init__(self, session: AsyncSession) -> None:
        self.departments = PostgresDepartmentRepository(session)
        self.positions = PostgresPositionRepository(session)
        self.locations = PostgresLocationRepository(session)

    async def seed(self, tenant_id: UUID, suffix: str) -> tuple[UUID, UUID, UUID]:
        department_id, position_id, location_id = uuid7(), uuid7(), uuid7()
        await self.departments.add(tenant_id, department_id, f"Dept {suffix}", None)
        await self.positions.add(tenant_id, position_id, f"Position {suffix}", None)
        await self.locations.add(
            tenant_id, location_id, f"Location {suffix}", None, "UTC"
        )
        return department_id, position_id, location_id


async def _add_employee(
    session: AsyncSession,
    tenant_id: UUID,
    refs: tuple[UUID, UUID, UUID],
    *,
    code: str,
    email: str,
    manager_id: UUID | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
) -> UUID:
    department_id, position_id, location_id = refs
    employee = await PostgresEmployeeRepository(session).add(
        tenant_id,
        uuid7(),
        employee_code=code,
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        phone=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=status,
        hired_at=HIRED_AT,
    )
    return employee.id


async def _seed_types(
    repo: PostgresLeaveRepository, tenant_id: UUID
) -> dict[LeaveTypeCode, UUID]:
    return {row.code: row.id for row in await repo.ensure_leave_types(tenant_id)}


async def _add_request(
    repo: PostgresLeaveRepository,
    tenant_id: UUID,
    *,
    employee_id: UUID,
    leave_type_id: UUID,
    start: date,
    end: date,
    days: int | None = None,
    reason: str | None = None,
) -> UUID:
    request_id = uuid7()
    await repo.add_request(
        tenant_id,
        request_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        start_date=start,
        end_date=end,
        days=days if days is not None else (end - start).days + 1,
        reason=reason,
    )
    return request_id


async def test_ensure_leave_types_is_idempotent_and_per_tenant(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)

    first = await repo.ensure_leave_types(TENANT_A)
    again = await repo.ensure_leave_types(TENANT_A)
    other_tenant = await repo.ensure_leave_types(TENANT_B)

    assert [row.code for row in first] == [
        LeaveTypeCode.ANNUAL,
        LeaveTypeCode.SICK,
        LeaveTypeCode.UNPAID,
    ]
    assert [row.id for row in first] == [row.id for row in again]
    assert {row.id for row in first}.isdisjoint({row.id for row in other_tenant})
    by_code = {row.code: row for row in first}
    assert by_code[LeaveTypeCode.ANNUAL].counts_against_balance is True
    assert by_code[LeaveTypeCode.SICK].counts_against_balance is True
    assert by_code[LeaveTypeCode.UNPAID].counts_against_balance is False


async def test_balances_are_lazy_idempotent_and_tenant_scoped(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)
    refs_a = await OrgRefs(session).seed(TENANT_A, "A")
    refs_b = await OrgRefs(session).seed(TENANT_B, "B")
    employee_a = await _add_employee(
        session, TENANT_A, refs_a, code="EMP-00001", email="a@example.test"
    )
    employee_b = await _add_employee(
        session, TENANT_B, refs_b, code="EMP-00001", email="b@example.test"
    )
    types_a = await _seed_types(repo, TENANT_A)

    annual = await repo.get_or_create_balance(
        TENANT_A, employee_a, types_a[LeaveTypeCode.ANNUAL], 2026
    )
    annual_again = await repo.get_or_create_balance(
        TENANT_A, employee_a, types_a[LeaveTypeCode.ANNUAL], 2026
    )
    sick = await repo.get_or_create_balance(
        TENANT_A, employee_a, types_a[LeaveTypeCode.SICK], 2026
    )

    assert annual is not None and annual_again is not None
    assert annual.entitlement_days == 12
    assert annual.used_days == 0
    assert annual.remaining_days == 12
    assert sick is not None and sick.entitlement_days == 6

    # Unknown type / another tenant's type are defensive `None`s, not leaks.
    assert await repo.get_or_create_balance(TENANT_A, employee_a, uuid7(), 2026) is None
    assert (
        await repo.get_or_create_balance(
            TENANT_B, employee_a, types_a[LeaveTypeCode.ANNUAL], 2026
        )
        is None
    )
    assert await repo.list_balances(TENANT_B, employee_ids=None, year=2026) == []

    balances = await repo.list_balances(TENANT_A, employee_ids=[employee_a], year=2026)
    assert [row.leave_type_id for row in balances] == [
        types_a[LeaveTypeCode.ANNUAL],
        types_a[LeaveTypeCode.SICK],
    ]
    assert (
        await repo.list_balances(TENANT_A, employee_ids=[employee_b], year=2026) == []
    )


async def test_add_and_get_request_join_the_type_code(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employee = await _add_employee(
        session, TENANT_A, refs, code="EMP-00001", email="a@example.test"
    )
    types = await _seed_types(repo, TENANT_A)
    request_id = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=types[LeaveTypeCode.ANNUAL],
        start=date(2026, 11, 2),
        end=date(2026, 11, 6),
        reason="Family trip",
    )

    request = await repo.get_request(TENANT_A, request_id)
    assert request is not None
    assert request.leave_type_code is LeaveTypeCode.ANNUAL
    assert request.start_date == date(2026, 11, 2)
    assert request.end_date == date(2026, 11, 6)
    assert request.days == 5
    assert request.status is LeaveRequestStatus.PENDING
    assert request.reason == "Family trip"
    assert request.decided_by is None and request.decided_at is None

    # Cross-tenant detail is a `None` (the service turns it into 404).
    assert await repo.get_request(TENANT_B, request_id) is None
    items, total = await repo.list_requests(
        TENANT_B, LeaveRequestFilters(), PageParams()
    )
    assert (items, total) == ([], 0)


async def test_find_overlapping_is_inclusive_and_ignores_closed(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employee = await _add_employee(
        session, TENANT_A, refs, code="EMP-00001", email="a@example.test"
    )
    types = await _seed_types(repo, TENANT_A)
    annual = types[LeaveTypeCode.ANNUAL]
    sick = types[LeaveTypeCode.SICK]

    pending = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=annual,
        start=date(2026, 11, 2),
        end=date(2026, 11, 6),
    )
    approved = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=sick,
        start=date(2026, 11, 10),
        end=date(2026, 11, 11),
    )
    await repo.transition_request(
        TENANT_A,
        approved,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.APPROVED,
        now=NOW,
        decided_by=uuid7(),
        decided_at=NOW,
    )
    rejected = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=annual,
        start=date(2026, 11, 20),
        end=date(2026, 11, 21),
    )
    await repo.transition_request(
        TENANT_A,
        rejected,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.REJECTED,
        now=NOW,
        decided_by=uuid7(),
        decided_at=NOW,
    )
    withdrawn = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=annual,
        start=date(2026, 11, 22),
        end=date(2026, 11, 22),
    )
    await repo.transition_request(
        TENANT_A,
        withdrawn,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.WITHDRAWN,
        now=NOW,
        employee_id=employee,
    )

    async def ids(start: date, end: date) -> list[UUID]:
        rows = await repo.find_overlapping(TENANT_A, employee, start, end)
        return [row.id for row in rows]

    # Inclusive endpoints on both sides.
    assert await ids(date(2026, 11, 6), date(2026, 11, 7)) == [pending]
    assert await ids(date(2026, 11, 1), date(2026, 11, 2)) == [pending]
    assert await ids(date(2026, 11, 7), date(2026, 11, 9)) == []
    assert await ids(date(2026, 10, 1), date(2026, 12, 1)) == [
        pending,
        approved,
    ]
    # Rejected/withdrawn ranges never block.
    assert await ids(date(2026, 11, 20), date(2026, 11, 22)) == []
    # Tenant scoping: B sees none of A's requests.
    assert (
        await repo.find_overlapping(
            TENANT_B, employee, date(2026, 10, 1), date(2026, 12, 1)
        )
        == []
    )


async def test_transition_is_conditional_and_withdraw_guards_owner(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employee = await _add_employee(
        session, TENANT_A, refs, code="EMP-00001", email="a@example.test"
    )
    colleague = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00002",
        email="b@example.test",
    )
    types = await _seed_types(repo, TENANT_A)
    decider = uuid7()

    request_id = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=types[LeaveTypeCode.ANNUAL],
        start=date(2026, 11, 2),
        end=date(2026, 11, 3),
    )
    approved = await repo.transition_request(
        TENANT_A,
        request_id,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.APPROVED,
        now=NOW,
        decided_by=decider,
        decided_at=NOW,
        decision_note="Enjoy",
    )
    assert approved is not None
    assert approved.status is LeaveRequestStatus.APPROVED
    assert approved.decided_by == decider
    assert approved.decided_at == NOW
    assert approved.decision_note == "Enjoy"
    assert approved.updated_at == NOW

    # The second decider loses the conditional transition.
    assert (
        await repo.transition_request(
            TENANT_A,
            request_id,
            from_status=LeaveRequestStatus.PENDING,
            to_status=LeaveRequestStatus.REJECTED,
            now=NOW,
        )
        is None
    )

    pending = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=types[LeaveTypeCode.ANNUAL],
        start=date(2026, 12, 1),
        end=date(2026, 12, 1),
    )
    # Withdraw is own-only: another employee's guard matches zero rows.
    assert (
        await repo.transition_request(
            TENANT_A,
            pending,
            from_status=LeaveRequestStatus.PENDING,
            to_status=LeaveRequestStatus.WITHDRAWN,
            now=NOW,
            employee_id=colleague,
        )
        is None
    )
    withdrawn = await repo.transition_request(
        TENANT_A,
        pending,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.WITHDRAWN,
        now=NOW,
        employee_id=employee,
    )
    assert withdrawn is not None
    assert withdrawn.status is LeaveRequestStatus.WITHDRAWN
    assert withdrawn.decided_by is None and withdrawn.decided_at is None

    # Cross-tenant transitions are invisible.
    assert (
        await repo.transition_request(
            TENANT_B,
            pending,
            from_status=LeaveRequestStatus.WITHDRAWN,
            to_status=LeaveRequestStatus.APPROVED,
            now=NOW,
        )
        is None
    )


async def test_deduct_balance_never_overdraws(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employee = await _add_employee(
        session, TENANT_A, refs, code="EMP-00001", email="a@example.test"
    )
    types = await _seed_types(repo, TENANT_A)
    annual = types[LeaveTypeCode.ANNUAL]
    balance = await repo.get_or_create_balance(TENANT_A, employee, annual, 2026)
    assert balance is not None and balance.entitlement_days == 12

    assert await repo.deduct_balance(TENANT_A, employee, annual, 2026, 12) is True
    exhausted = await repo.get_or_create_balance(TENANT_A, employee, annual, 2026)
    assert exhausted is not None
    assert exhausted.used_days == 12
    assert exhausted.remaining_days == 0

    # No row (or an overdraw) is a `False`, never a negative balance.
    assert await repo.deduct_balance(TENANT_A, employee, annual, 2026, 1) is False
    assert await repo.deduct_balance(TENANT_A, employee, annual, 2027, 1) is False
    assert await repo.deduct_balance(TENANT_B, employee, annual, 2026, 1) is False


async def test_list_requests_filters_and_stable_pagination(
    session: AsyncSession,
) -> None:
    repo = PostgresLeaveRepository(session)
    refs = await OrgRefs(session).seed(TENANT_A, "A")
    employee = await _add_employee(
        session, TENANT_A, refs, code="EMP-00001", email="a@example.test"
    )
    colleague = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00002",
        email="b@example.test",
    )
    types = await _seed_types(repo, TENANT_A)
    annual = types[LeaveTypeCode.ANNUAL]

    first = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=annual,
        start=date(2026, 11, 2),
        end=date(2026, 11, 3),
    )
    second = await _add_request(
        repo,
        TENANT_A,
        employee_id=employee,
        leave_type_id=annual,
        start=date(2026, 12, 7),
        end=date(2026, 12, 8),
    )
    third = await _add_request(
        repo,
        TENANT_A,
        employee_id=colleague,
        leave_type_id=annual,
        start=date(2026, 11, 15),
        end=date(2026, 11, 15),
    )

    # Newest created first; equal transaction timestamps fall back to id desc.
    items, total = await repo.list_requests(
        TENANT_A, LeaveRequestFilters(employee_ids=[employee]), PageParams()
    )
    assert total == 2
    assert [row.id for row in items] == [second, first]

    page_one, total = await repo.list_requests(
        TENANT_A,
        LeaveRequestFilters(employee_ids=[employee]),
        PageParams(page=1, page_size=1),
    )
    page_two, _ = await repo.list_requests(
        TENANT_A,
        LeaveRequestFilters(employee_ids=[employee]),
        PageParams(page=2, page_size=1),
    )
    assert total == 2
    assert [row.id for row in page_one] == [second]
    assert [row.id for row in page_two] == [first]

    # Overlap window: December 7 touches the second range's start.
    window, _ = await repo.list_requests(
        TENANT_A,
        LeaveRequestFilters(
            employee_ids=[employee],
            from_date=date(2026, 12, 7),
            to_date=date(2026, 12, 31),
        ),
        PageParams(),
    )
    assert [row.id for row in window] == [second]

    # Unknown employee / empty scope matches nothing; tenant B sees nothing.
    assert await repo.list_requests(
        TENANT_A, LeaveRequestFilters(employee_ids=[uuid7()]), PageParams()
    ) == ([], 0)
    assert await repo.list_requests(
        TENANT_A, LeaveRequestFilters(employee_ids=[]), PageParams()
    ) == ([], 0)
    assert await repo.list_requests(TENANT_B, LeaveRequestFilters(), PageParams()) == (
        [],
        0,
    )

    # Status filter is effective.
    await repo.transition_request(
        TENANT_A,
        third,
        from_status=LeaveRequestStatus.PENDING,
        to_status=LeaveRequestStatus.REJECTED,
        now=NOW,
        decided_by=uuid7(),
        decided_at=NOW,
    )
    rejected, total = await repo.list_requests(
        TENANT_A,
        LeaveRequestFilters(status=LeaveRequestStatus.REJECTED),
        PageParams(),
    )
    assert total == 1 and [row.id for row in rejected] == [third]
