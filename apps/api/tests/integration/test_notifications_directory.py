"""Recipient-directory integration tests for Notifications (T3).

The directory is the read-only seam that turns identifiers from the event
stream into display names, a direct manager and the linked account address
(ADR 0015 decision 1/8). These tests run it against real `employees`/`users`
rows: manager resolution, account resolution through `users.email`,
non-employee actors and tenant scoping.
"""

from datetime import date
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.notifications.schemas import Recipient
from app.infrastructure.postgres.recipient_directory import (
    PostgresRecipientDirectory,
)
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.identity import PostgresUserRepository
from app.infrastructure.postgres.repositories.organizations import (
    PostgresDepartmentRepository,
    PostgresLocationRepository,
    PostgresPositionRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import TENANT_A, TENANT_B

HIRED_AT = date(2026, 1, 5)


async def _seed_refs(
    session: AsyncSession, tenant_id: UUID, suffix: str
) -> tuple[UUID, UUID, UUID]:
    departments = PostgresDepartmentRepository(session)
    positions = PostgresPositionRepository(session)
    locations = PostgresLocationRepository(session)
    department_id, position_id, location_id = uuid7(), uuid7(), uuid7()
    await departments.add(tenant_id, department_id, f"Dept {suffix}", None)
    await positions.add(tenant_id, position_id, f"Position {suffix}", None)
    await locations.add(tenant_id, location_id, f"Location {suffix}", None, "UTC")
    return department_id, position_id, location_id


async def _add_employee(
    session: AsyncSession,
    tenant_id: UUID,
    refs: tuple[UUID, UUID, UUID],
    *,
    code: str,
    first_name: str,
    last_name: str,
    email: str,
    manager_id: UUID | None = None,
) -> UUID:
    department_id, position_id, location_id = refs
    employee = await PostgresEmployeeRepository(session).add(
        tenant_id,
        uuid7(),
        employee_code=code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=None,
        department_id=department_id,
        position_id=position_id,
        location_id=location_id,
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=HIRED_AT,
    )
    return employee.id


async def _add_user(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    employee_id: UUID | None,
    email: str,
    user_id: UUID | None = None,
) -> UUID:
    user = await PostgresUserRepository(session).add(
        tenant_id,
        user_id if user_id is not None else uuid7(),
        email=email,
        role=Role.EMPLOYEE,
        employee_id=employee_id,
        password_hash="not-used-in-directory-tests",
    )
    return user.id


async def test_subject_facts_resolves_name_and_manager(session: AsyncSession) -> None:
    directory = PostgresRecipientDirectory(session)
    refs = await _seed_refs(session, TENANT_A, "A")
    manager = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00001",
        first_name="Mary",
        last_name="Manager",
        email="mary.manager@example.test",
    )
    employee = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00002",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        manager_id=manager,
    )
    unmanaged = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00003",
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
    )

    facts = await directory.subject_facts(TENANT_A, employee)
    assert facts is not None
    assert facts.display_name == "Ada Lovelace"
    assert facts.manager_id == manager

    unmanaged_facts = await directory.subject_facts(TENANT_A, unmanaged)
    assert unmanaged_facts is not None
    assert unmanaged_facts.manager_id is None

    # Unknown and cross-tenant ids are `None`, never another tenant's person.
    assert await directory.subject_facts(TENANT_A, uuid7()) is None
    assert await directory.subject_facts(TENANT_B, employee) is None


async def test_recipient_uses_the_linked_account_address(
    session: AsyncSession,
) -> None:
    directory = PostgresRecipientDirectory(session)
    refs = await _seed_refs(session, TENANT_A, "A")
    employee = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00002",
        first_name="Ada",
        last_name="Lovelace",
        email="ada.work@example.test",
    )

    # No linked account → person-addressed delivery is impossible.
    assert await directory.recipient(TENANT_A, employee) is None

    user_id = await _add_user(
        session,
        TENANT_A,
        employee_id=employee,
        email="ada.account@example.test",
    )
    recipient = await directory.recipient(TENANT_A, employee)
    assert recipient == Recipient(
        employee_id=employee,
        user_id=user_id,
        display_name="Ada Lovelace",
        # The account address, deliberately not `employees.email`.
        account_address="ada.account@example.test",
    )

    # Tenant scoping and unknown employees resolve to `None`.
    assert await directory.recipient(TENANT_B, employee) is None
    assert await directory.recipient(TENANT_A, uuid7()) is None


async def test_actor_display_name_only_resolves_linked_accounts(
    session: AsyncSession,
) -> None:
    directory = PostgresRecipientDirectory(session)
    refs = await _seed_refs(session, TENANT_A, "A")
    employee = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00002",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
    )
    linked_user = await _add_user(
        session, TENANT_A, employee_id=employee, email="ada.account@example.test"
    )
    unlinked_user = await _add_user(
        session, TENANT_A, employee_id=None, email="admin@example.test"
    )

    assert await directory.actor_display_name(TENANT_A, linked_user) == "Ada Lovelace"
    # Admin/HR accounts have no employee identity → the template omits the clause.
    assert await directory.actor_display_name(TENANT_A, unlinked_user) is None
    assert await directory.actor_display_name(TENANT_A, uuid7()) is None

    other_refs = await _seed_refs(session, TENANT_B, "B")
    other_employee = await _add_employee(
        session,
        TENANT_B,
        other_refs,
        code="EMP-00001",
        first_name="Other",
        last_name="Person",
        email="other@example.test",
    )
    other_user = await _add_user(
        session,
        TENANT_B,
        employee_id=other_employee,
        email="other.account@example.test",
    )
    assert await directory.actor_display_name(TENANT_A, other_user) is None


async def test_multiple_linked_accounts_pick_lowest_id_and_log(
    session: AsyncSession,
) -> None:
    """Spec R6: identity forbids this at the service level; stay deterministic."""
    directory = PostgresRecipientDirectory(session)
    refs = await _seed_refs(session, TENANT_A, "A")
    employee = await _add_employee(
        session,
        TENANT_A,
        refs,
        code="EMP-00002",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
    )
    first_id = UUID("00000000-0000-4000-8000-0000000000a1")
    second_id = UUID("00000000-0000-4000-8000-0000000000a2")
    await _add_user(
        session,
        TENANT_A,
        employee_id=employee,
        email="second@example.test",
        user_id=second_id,
    )
    await _add_user(
        session,
        TENANT_A,
        employee_id=employee,
        email="first@example.test",
        user_id=first_id,
    )

    structlog.configure(cache_logger_on_first_use=False)
    with structlog.testing.capture_logs() as logs:
        recipient = await directory.recipient(TENANT_A, employee)

    assert recipient is not None
    assert recipient.user_id == first_id
    assert recipient.account_address == "first@example.test"
    warnings = [
        entry for entry in logs if entry.get("event") == "recipient_link_ambiguous"
    ]
    assert len(warnings) == 1
    assert warnings[0]["employee_id"] == str(employee)
    assert warnings[0]["linked_accounts"] == 2
    # Ids only — no email address or display name in the log line.
    dumped = str(warnings[0])
    assert "first@example.test" not in dumped
    assert "Ada Lovelace" not in dumped
