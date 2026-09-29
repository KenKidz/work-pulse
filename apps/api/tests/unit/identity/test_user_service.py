"""Unit tests for UserService — user management rules (T3, no DB)."""

import json

import pytest
from pydantic import ValidationError
from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.identity.credentials import session_token_hash, verify_password
from app.domains.identity.exceptions import (
    EmployeeLinkTakenError,
    LastAdminError,
    UserEmailTakenError,
    UserNotFoundError,
)
from app.domains.identity.repository import UserFilters
from app.domains.identity.schemas import (
    ChangePasswordRequest,
    CreateUser,
    LoginRequest,
    ResetPasswordRequest,
    UpdateUser,
)
from app.domains.identity.service import AuthService, UserService
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.identity.conftest import (
    ACTOR,
    DEFAULT_PASSWORD,
    NOW,
    TENANT_A,
    TENANT_B,
    identity,
    seed_user,
)
from tests.unit.identity.fakes import (
    FakeEmployeeLinkPort,
    FakeSessionRepo,
    FakeUserRepo,
    RecordingAuditWriter,
)


def test_password_minimum_is_enforced_by_the_contract() -> None:
    with pytest.raises(ValidationError):
        CreateUser(email="short@workpulse.test", password="ninechars", role=Role.HR)
    with pytest.raises(ValidationError):
        ResetPasswordRequest(new_password="ninechars")
    with pytest.raises(ValidationError):
        ChangePasswordRequest(
            current_password=DEFAULT_PASSWORD, new_password="ninechars"
        )
    with pytest.raises(ValidationError):
        # Explicit null is not "unchanged" for required PATCH fields.
        UpdateUser(email=None)

    CreateUser(email="ok@workpulse.test", password="ten-chars!", role=Role.HR)


async def test_list_filters_order_and_paging(
    service: UserService, users: FakeUserRepo
) -> None:
    await seed_user(users, email="grace@workpulse.test", role=Role.HR)
    await seed_user(users, email="ada@workpulse.test", role=Role.ADMIN)
    await seed_user(
        users, email="alan@workpulse.test", role=Role.EMPLOYEE, active=False
    )
    await seed_user(
        users, tenant_id=TENANT_B, email="b@workpulse.test", role=Role.ADMIN
    )

    all_users = await service.list(identity(), UserFilters(), PageParams())
    assert [item.email for item in all_users.items] == [
        "ada@workpulse.test",
        "alan@workpulse.test",
        "grace@workpulse.test",
    ]
    assert all_users.page.total_items == 3 and all_users.page.total_pages == 1

    by_q = await service.list(identity(), UserFilters(q="ADA@"), PageParams())
    assert [item.email for item in by_q.items] == ["ada@workpulse.test"]

    by_role = await service.list(identity(), UserFilters(role=Role.ADMIN), PageParams())
    assert [item.email for item in by_role.items] == ["ada@workpulse.test"]

    by_active = await service.list(identity(), UserFilters(active=False), PageParams())
    assert [item.email for item in by_active.items] == ["alan@workpulse.test"]

    first = await service.list(
        identity(), UserFilters(), PageParams(page=1, page_size=2)
    )
    second = await service.list(
        identity(), UserFilters(), PageParams(page=2, page_size=2)
    )
    assert [item.email for item in first.items] == [
        "ada@workpulse.test",
        "alan@workpulse.test",
    ]
    assert [item.email for item in second.items] == ["grace@workpulse.test"]
    assert second.page.total_pages == 2


async def test_get_and_mutations_are_tenant_scoped(
    service: UserService, users: FakeUserRepo
) -> None:
    user_a = await seed_user(users, email="a@workpulse.test")
    tenant_b = identity(tenant_id=TENANT_B)

    with pytest.raises(UserNotFoundError) as exc:
        await service.get(tenant_b, user_a.id)
    assert (exc.value.status_code, exc.value.code) == (404, "user_not_found")

    with pytest.raises(UserNotFoundError):
        await service.update(tenant_b, user_a.id, UpdateUser(role=Role.HR), NOW)
    with pytest.raises(UserNotFoundError):
        await service.activate(tenant_b, user_a.id, NOW)
    with pytest.raises(UserNotFoundError):
        await service.deactivate(tenant_b, user_a.id, NOW)
    with pytest.raises(UserNotFoundError):
        await service.reset_password(
            tenant_b,
            user_a.id,
            ResetPasswordRequest(new_password="another-good-password"),
            NOW,
        )

    listed = await service.list(tenant_b, UserFilters(), PageParams())
    assert listed.items == []


async def test_create_hashes_the_password_and_audits(
    service: UserService, users: FakeUserRepo, audit: RecordingAuditWriter
) -> None:
    created = await service.create(
        identity(),
        CreateUser(email="new@workpulse.test", password=DEFAULT_PASSWORD, role=Role.HR),
        NOW,
    )

    assert created.email == "new@workpulse.test"
    assert created.role is Role.HR and created.active is True
    assert "password_hash" not in created.model_dump()
    stored = await users.get_credentials(TENANT_A, created.id)
    assert stored is not None
    assert await verify_password(stored.password_hash, DEFAULT_PASSWORD)

    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert (
        entry.action,
        entry.entity_type,
        entry.entity_id,
        entry.actor_id,
    ) == ("user.created", "user", created.id, ACTOR)
    assert entry.changes is not None and entry.changes["role"] == {
        "old": None,
        "new": "hr",
    }
    serialized = json.dumps(entry.model_dump(mode="json"))
    assert DEFAULT_PASSWORD not in serialized
    assert stored.password_hash not in serialized


async def test_create_rejects_a_globally_taken_email(
    service: UserService, users: FakeUserRepo, audit: RecordingAuditWriter
) -> None:
    await seed_user(users, tenant_id=TENANT_B, email="taken@workpulse.test")

    with pytest.raises(UserEmailTakenError) as exc:
        await service.create(
            identity(),
            CreateUser(
                email="taken@workpulse.test", password=DEFAULT_PASSWORD, role=Role.HR
            ),
            NOW,
        )

    assert (exc.value.status_code, exc.value.code) == (409, "user_email_taken")
    assert audit.entries == []


async def test_create_employee_link_rules(
    service: UserService, links: FakeEmployeeLinkPort
) -> None:
    employee_id = uuid7()
    links.seed(TENANT_A, employee_id)

    linked = await service.create(
        identity(),
        CreateUser(
            email="linked@workpulse.test",
            password=DEFAULT_PASSWORD,
            role=Role.EMPLOYEE,
            employee_id=employee_id,
        ),
        NOW,
    )
    assert linked.employee_id == employee_id

    # The same employee cannot be linked twice.
    with pytest.raises(EmployeeLinkTakenError) as taken:
        await service.create(
            identity(),
            CreateUser(
                email="second@workpulse.test",
                password=DEFAULT_PASSWORD,
                role=Role.EMPLOYEE,
                employee_id=employee_id,
            ),
            NOW,
        )
    assert (taken.value.status_code, taken.value.code) == (409, "employee_link_taken")

    # A missing employee and another tenant's employee answer identically.
    cross = uuid7()
    links.seed(TENANT_B, cross)
    for unknown in (uuid7(), cross):
        with pytest.raises(EmployeeNotFoundError) as missing:
            await service.create(
                identity(),
                CreateUser(
                    email="ghost@workpulse.test",
                    password=DEFAULT_PASSWORD,
                    role=Role.EMPLOYEE,
                    employee_id=unknown,
                ),
                NOW,
            )
        assert (missing.value.status_code, missing.value.code) == (
            404,
            "employee_not_found",
        )


async def test_update_patch_semantics(
    service: UserService,
    users: FakeUserRepo,
    links: FakeEmployeeLinkPort,
    audit: RecordingAuditWriter,
) -> None:
    user = await seed_user(users, email="ada@workpulse.test", role=Role.EMPLOYEE)
    employee_id = uuid7()
    links.seed(TENANT_A, employee_id)

    # Absent fields are unchanged and nothing is audited.
    unchanged = await service.update(identity(), user.id, UpdateUser(), NOW)
    assert unchanged.email == user.email and unchanged.role is Role.EMPLOYEE
    assert audit.entries == []

    linked = await service.update(
        identity(), user.id, UpdateUser(employee_id=employee_id), NOW
    )
    assert linked.employee_id == employee_id

    # Explicit `"employee_id": null` unlinks.
    unlinked = await service.update(
        identity(), user.id, UpdateUser(employee_id=None), NOW
    )
    assert unlinked.employee_id is None

    rebalanced = await service.update(
        identity(),
        user.id,
        UpdateUser(email="ada2@workpulse.test", role=Role.HR),
        NOW,
    )
    assert rebalanced.email == "ada2@workpulse.test" and rebalanced.role is Role.HR

    assert [entry.action for entry in audit.entries] == ["user.updated"] * 3
    # PII rule: field names only, never values.
    assert audit.entries[0].changes == {"employee_id": "changed"}
    assert audit.entries[1].changes == {"employee_id": "changed"}
    assert audit.entries[2].changes == {"email": "changed", "role": "changed"}


async def test_update_email_change_rechecks_global_uniqueness(
    service: UserService, users: FakeUserRepo
) -> None:
    user = await seed_user(users, email="ada@workpulse.test")
    await seed_user(users, tenant_id=TENANT_B, email="grace@workpulse.test")

    with pytest.raises(UserEmailTakenError):
        await service.update(
            identity(), user.id, UpdateUser(email="grace@workpulse.test"), NOW
        )

    # Keeping one's own email is not a conflict.
    updated = await service.update(
        identity(), user.id, UpdateUser(email="ada@workpulse.test"), NOW
    )
    assert updated.email == "ada@workpulse.test"


async def test_update_link_rules_and_separate_employment_lifecycle(
    service: UserService, users: FakeUserRepo, links: FakeEmployeeLinkPort
) -> None:
    employee_id = uuid7()
    links.seed(TENANT_A, employee_id)
    await seed_user(users, email="holder@workpulse.test", employee_id=employee_id)
    user = await seed_user(users, email="ada@workpulse.test")

    with pytest.raises(EmployeeLinkTakenError):
        await service.update(
            identity(), user.id, UpdateUser(employee_id=employee_id), NOW
        )

    # Linking a terminated employee is allowed: the port answers existence
    # only, and accounts and employment are separate lifecycles (spec edge
    # case — the employee must merely exist in the tenant).
    other_employee = uuid7()
    links.seed(TENANT_A, other_employee)
    linked = await service.update(
        identity(), user.id, UpdateUser(employee_id=other_employee), NOW
    )
    assert linked.employee_id == other_employee

    cross = uuid7()
    links.seed(TENANT_B, cross)
    with pytest.raises(EmployeeNotFoundError):
        await service.update(identity(), user.id, UpdateUser(employee_id=cross), NOW)


async def test_deactivate_and_activate_roundtrip_with_audit(
    service: UserService, users: FakeUserRepo, audit: RecordingAuditWriter
) -> None:
    await seed_user(users, email="admin1@workpulse.test", role=Role.ADMIN)
    admin2 = await seed_user(users, email="admin2@workpulse.test", role=Role.ADMIN)

    deactivated = await service.deactivate(identity(), admin2.id, NOW)
    assert deactivated.active is False
    assert audit.entries[-1].action == "user.deactivated"
    assert audit.entries[-1].changes == {"active": {"old": True, "new": False}}

    entries = len(audit.entries)
    again = await service.deactivate(identity(), admin2.id, NOW)
    assert again.active is False and len(audit.entries) == entries  # no-op

    activated = await service.activate(identity(), admin2.id, NOW)
    assert activated.active is True
    assert audit.entries[-1].action == "user.activated"
    assert audit.entries[-1].changes == {"active": {"old": False, "new": True}}

    entries = len(audit.entries)
    assert (await service.activate(identity(), admin2.id, NOW)).active is True
    assert len(audit.entries) == entries  # no-op


async def test_deactivating_the_last_admin_is_rejected(
    service: UserService, users: FakeUserRepo, audit: RecordingAuditWriter
) -> None:
    only_admin = await seed_user(users, email="only@workpulse.test", role=Role.ADMIN)

    with pytest.raises(LastAdminError) as exc:
        await service.deactivate(identity(), only_admin.id, NOW)

    assert (exc.value.status_code, exc.value.code) == (409, "last_admin")
    current = await users.get(TENANT_A, only_admin.id)
    assert current is not None and current.active is True
    assert audit.entries == []


async def test_super_admin_is_protected_and_counts_are_tenant_scoped(
    service: UserService, users: FakeUserRepo
) -> None:
    super_admin = await seed_user(
        users, email="super@workpulse.test", role=Role.SUPER_ADMIN
    )
    # An active admin of another tenant does not count for this tenant.
    await seed_user(
        users, tenant_id=TENANT_B, email="admin.b@workpulse.test", role=Role.ADMIN
    )

    with pytest.raises(LastAdminError):
        await service.deactivate(identity(), super_admin.id, NOW)

    # A sole non-admin is not covered by the guard.
    employee = await seed_user(users, email="worker@workpulse.test", role=Role.EMPLOYEE)
    assert (await service.deactivate(identity(), employee.id, NOW)).active is False


async def test_second_admin_can_be_deactivated(
    service: UserService, users: FakeUserRepo
) -> None:
    first = await seed_user(users, email="first@workpulse.test", role=Role.ADMIN)
    second = await seed_user(users, email="second@workpulse.test", role=Role.ADMIN)

    assert (await service.deactivate(identity(), first.id, NOW)).active is False
    with pytest.raises(LastAdminError):
        await service.deactivate(identity(), second.id, NOW)


async def test_reset_password_rehashes_revokes_sessions_and_audits(
    service: UserService,
    auth: AuthService,
    users: FakeUserRepo,
    sessions: FakeSessionRepo,
    audit: RecordingAuditWriter,
    default_password_hash: str,
) -> None:
    target = await seed_user(
        users,
        email="worker@workpulse.test",
        role=Role.EMPLOYEE,
        password_hash=default_password_hash,
    )
    _, target_token = await auth.login(
        LoginRequest(email=target.email, password=DEFAULT_PASSWORD), NOW
    )

    new_password = "admin-issued-password"
    await service.reset_password(
        identity(), target.id, ResetPasswordRequest(new_password=new_password), NOW
    )

    stored = await users.get_credentials(TENANT_A, target.id)
    assert stored is not None
    assert await verify_password(stored.password_hash, new_password)
    assert not await verify_password(stored.password_hash, DEFAULT_PASSWORD)
    assert (
        await sessions.get_by_token_hash(session_token_hash(target_token), NOW) is None
    )

    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert (entry.action, entry.entity_type) == ("user.password_reset", "user")
    assert entry.entity_id == target.id and entry.actor_id == ACTOR
    serialized = json.dumps(entry.model_dump(mode="json"))
    assert DEFAULT_PASSWORD not in serialized and new_password not in serialized
