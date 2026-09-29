"""Repository integration tests (T2): tenant scoping, global email uniqueness,
session lookup/revocation, and the EmployeeLinkPort over real Postgres.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.identity.exceptions import UserEmailTakenError
from app.domains.identity.repository import UserFilters
from app.domains.identity.schemas import User
from app.infrastructure.postgres.employee_link import PostgresEmployeeLink
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.identity import (
    PostgresSessionRepository,
    PostgresUserRepository,
)
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.integration.conftest import TENANT_A, TENANT_B

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
TTL = timedelta(days=7)


@pytest.fixture
def users(session: AsyncSession) -> PostgresUserRepository:
    return PostgresUserRepository(session)


@pytest.fixture
def sessions(session: AsyncSession) -> PostgresSessionRepository:
    return PostgresSessionRepository(session)


async def _add_user(
    users: PostgresUserRepository,
    tenant_id: UUID,
    *,
    email: str,
    role: Role = Role.EMPLOYEE,
    employee_id: UUID | None = None,
    active: bool = True,
    password_hash: str = "argon2-placeholder",
) -> User:
    return await users.add(
        tenant_id,
        uuid7(),
        email=email,
        role=role,
        employee_id=employee_id,
        password_hash=password_hash,
        active=active,
    )


async def _add_employee(session: AsyncSession, tenant_id: UUID) -> UUID:
    """One employee of the tenant; returns its id (for the link port tests)."""
    departments = PostgresDepartmentRepository(session)
    positions = PostgresPositionRepository(session)
    locations = PostgresLocationRepository(session)
    employees = PostgresEmployeeRepository(session)
    department_id, position_id, location_id = uuid7(), uuid7(), uuid7()
    suffix = uuid7().hex[:8]
    await departments.add(tenant_id, department_id, f"Dept {suffix}", None)
    await positions.add(tenant_id, position_id, f"Position {suffix}", None)
    await locations.add(tenant_id, location_id, f"Location {suffix}", None, "UTC")
    employee = await employees.add(
        tenant_id,
        uuid7(),
        employee_code=f"EMP-{suffix}",
        first_name="Ada",
        last_name="Lovelace",
        email=f"ada.{suffix}@example.test",
        phone=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )
    return employee.id


async def test_user_scoping_both_directions(
    users: PostgresUserRepository,
) -> None:
    user_a = await _add_user(users, TENANT_A, email="a@workpulse.test")
    user_b = await _add_user(users, TENANT_B, email="b@workpulse.test")

    assert (await users.get(TENANT_A, user_a.id)) is not None
    assert (await users.get(TENANT_B, user_a.id)) is None  # A invisible to B
    assert (await users.get(TENANT_A, user_b.id)) is None

    # The credential projection (session resolution) is tenant-scoped too.
    assert (await users.get_credentials(TENANT_A, user_a.id)) is not None
    assert (await users.get_credentials(TENANT_B, user_a.id)) is None

    items_a, total_a = await users.list(TENANT_A, UserFilters(), PageParams())
    items_b, total_b = await users.list(TENANT_B, UserFilters(), PageParams())
    assert total_a == 1 and [item.id for item in items_a] == [user_a.id]
    assert total_b == 1 and [item.id for item in items_b] == [user_b.id]

    assert await users.update(TENANT_B, user_a.id, active=False) is None


async def test_email_is_globally_unique_across_tenants(
    users: PostgresUserRepository, session: AsyncSession
) -> None:
    first = await _add_user(users, TENANT_A, email="same@workpulse.test")

    assert await users.email_taken("same@workpulse.test") is True
    assert await users.email_taken("other@workpulse.test") is False
    assert await users.email_taken("same@workpulse.test", exclude_id=first.id) is False

    # Login's lookup is global on purpose (the account resolves the tenant)
    # and returns the credential projection: wire user + password hash.
    found = await users.get_by_email("same@workpulse.test")
    assert (
        found is not None
        and found.user.id == first.id
        and found.user.tenant_id == TENANT_A
        and found.password_hash == "argon2-placeholder"
    )
    assert await users.get_by_email("missing@workpulse.test") is None

    # Global uniqueness (decision 7): the other tenant cannot reuse the email.
    with pytest.raises(UserEmailTakenError):
        await _add_user(users, TENANT_B, email="same@workpulse.test")
    await session.rollback()


async def test_list_filters_ordering_and_paging(
    users: PostgresUserRepository,
) -> None:
    await _add_user(users, TENANT_A, email="grace@workpulse.test", role=Role.HR)
    await _add_user(users, TENANT_A, email="ada@workpulse.test", role=Role.ADMIN)
    await _add_user(
        users, TENANT_A, email="alan@workpulse.test", role=Role.EMPLOYEE, active=False
    )

    # Default order: email ascending.
    items, total = await users.list(TENANT_A, UserFilters(), PageParams())
    assert total == 3
    assert [item.email for item in items] == [
        "ada@workpulse.test",
        "alan@workpulse.test",
        "grace@workpulse.test",
    ]

    # q — case-insensitive substring of exactly the email.
    by_q, total_q = await users.list(TENANT_A, UserFilters(q="GRACE"), PageParams())
    assert total_q == 1 and by_q[0] is not None
    assert by_q[0].email == "grace@workpulse.test"

    by_role, total_role = await users.list(
        TENANT_A, UserFilters(role=Role.ADMIN), PageParams()
    )
    assert total_role == 1 and by_role[0] is not None
    assert by_role[0].role == Role.ADMIN

    by_active, total_active = await users.list(
        TENANT_A, UserFilters(active=False), PageParams()
    )
    assert total_active == 1 and by_active[0] is not None
    assert by_active[0].email == "alan@workpulse.test"

    page1, _ = await users.list(
        TENANT_A, UserFilters(), PageParams(page=1, page_size=2)
    )
    page2, _ = await users.list(
        TENANT_A, UserFilters(), PageParams(page=2, page_size=2)
    )
    assert [item.email for item in page1] == [
        "ada@workpulse.test",
        "alan@workpulse.test",
    ]
    assert [item.email for item in page2] == ["grace@workpulse.test"]


async def test_count_active_admins_is_tenant_scoped(
    users: PostgresUserRepository,
) -> None:
    await _add_user(users, TENANT_A, email="admin.a@workpulse.test", role=Role.ADMIN)
    await _add_user(
        users, TENANT_A, email="super.a@workpulse.test", role=Role.SUPER_ADMIN
    )
    await _add_user(users, TENANT_A, email="hr.a@workpulse.test", role=Role.HR)
    await _add_user(
        users,
        TENANT_A,
        email="inactive.a@workpulse.test",
        role=Role.ADMIN,
        active=False,
    )
    await _add_user(users, TENANT_B, email="admin.b@workpulse.test", role=Role.ADMIN)

    assert await users.count_active_admins(TENANT_A) == 2
    assert await users.count_active_admins(TENANT_B) == 1


async def test_employee_link_taken_and_port(
    users: PostgresUserRepository, session: AsyncSession
) -> None:
    employee_id = await _add_employee(session, TENANT_A)
    linked = await _add_user(
        users, TENANT_A, email="linked@workpulse.test", employee_id=employee_id
    )

    assert await users.employee_link_taken(TENANT_A, employee_id) is True
    assert (
        await users.employee_link_taken(TENANT_A, employee_id, exclude_id=linked.id)
        is False
    )
    assert await users.employee_link_taken(TENANT_B, employee_id) is False

    link = PostgresEmployeeLink(session)
    assert await link.exists(TENANT_A, employee_id) is True
    assert await link.exists(TENANT_B, employee_id) is False  # cross-tenant 404
    assert await link.exists(TENANT_A, uuid7()) is False


async def test_session_lookup_expiry_revocation_and_tenant_scope(
    users: PostgresUserRepository, sessions: PostgresSessionRepository
) -> None:
    user_a = await _add_user(users, TENANT_A, email="session.a@workpulse.test")
    user_b = await _add_user(users, TENANT_B, email="session.b@workpulse.test")

    valid = await sessions.add(
        TENANT_A,
        uuid7(),
        user_id=user_a.id,
        token_hash="hash-valid",
        expires_at=NOW + TTL,
    )
    other = await sessions.add(
        TENANT_A,
        uuid7(),
        user_id=user_a.id,
        token_hash="hash-other",
        expires_at=NOW + TTL,
    )
    await sessions.add(
        TENANT_A,
        uuid7(),
        user_id=user_a.id,
        token_hash="hash-expired",
        expires_at=NOW - timedelta(seconds=1),
    )
    session_b = await sessions.add(
        TENANT_B,
        uuid7(),
        user_id=user_b.id,
        token_hash="hash-b",
        expires_at=NOW + TTL,
    )

    # Lookup by the secret-derived key; expired and unknown rows are rejected.
    found = await sessions.get_by_token_hash("hash-valid", NOW)
    assert found is not None and found.id == valid.id and found.tenant_id == TENANT_A
    assert await sessions.get_by_token_hash("hash-expired", NOW) is None
    assert await sessions.get_by_token_hash("hash-unknown", NOW) is None
    # An expired row is only rejected relative to `now` — never ambiguous.
    assert await sessions.get_by_token_hash("hash-expired", NOW - timedelta(days=1))

    # Logout deletes by token hash and is idempotent.
    await sessions.delete("hash-valid")
    await sessions.delete("hash-valid")
    assert await sessions.get_by_token_hash("hash-valid", NOW) is None

    # Revocation is tenant-scoped: all of A's user's remaining sessions go,
    # B's survive.
    deleted = await sessions.delete_for_user(TENANT_A, user_a.id)
    assert deleted == 2  # `hash-other` + `hash-expired`
    assert await sessions.get_by_token_hash("hash-other", NOW) is None
    assert (
        await sessions.get_by_token_hash("hash-expired", NOW - timedelta(days=1))
        is None
    )
    assert (await sessions.get_by_token_hash("hash-b", NOW)) is not None
    assert other.user_id == user_a.id and session_b.user_id == user_b.id
