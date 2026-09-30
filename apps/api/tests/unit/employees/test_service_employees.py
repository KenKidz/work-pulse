"""Unit tests for EmployeeService — every business rule and edge case in the spec."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

import pytest
from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import (
    EmployeeAlreadyTerminatedError,
    EmployeeCodeTakenError,
    EmployeeEmailTakenError,
    EmployeeManagerInvalidError,
    EmployeeNotFoundError,
    EmployeeSelfManagerError,
    EmployeeTransferEffectiveInvalidError,
    EmploymentPeriodOverlapError,
    TransferTargetArchivedError,
)
from app.domains.employees.repository import EmployeeFilters
from app.domains.employees.schemas import (
    EmployeeStatus,
    EmploymentType,
    TerminationRequest,
    TransferRequest,
    UpdateEmployee,
)
from app.domains.employees.service import EmployeeService
from app.domains.organizations.exceptions import (
    DepartmentNotFoundError,
    LocationNotFoundError,
    PositionNotFoundError,
)
from app.shared.audit import AuditEntryCreate
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.employees.conftest import (
    EMPLOYEE_ID,
    HIRED_AT,
    NOW,
    TENANT_A,
    TENANT_B,
    TODAY,
    OrgWorld,
    create_employee,
    identity,
    seed_actor_employee,
)
from tests.unit.employees.fakes import FakeEmployeeRepo, FakeEmploymentRepo
from tests.unit.organizations.fakes import RecordingAuditWriter

LATER = date(2026, 1, 1)
ARCHIVED_AT = date(2026, 8, 1)


def _report_ids(entries: Sequence[AuditEntryCreate]) -> list[UUID]:
    return [
        entry.entity_id
        for entry in entries
        if entry.action == "employee.manager_cleared"
    ]


# --- create -------------------------------------------------------------------


async def test_create_auto_code_period_and_audit(
    service: EmployeeService,
    repo: FakeEmployeeRepo,
    employments: FakeEmploymentRepo,
    audit: RecordingAuditWriter,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()

    created = await create_employee(service, refs)

    assert created.employee_code == "EMP-00001"
    assert created.status == EmployeeStatus.ACTIVE
    assert created.terminated_at is None
    assert created.department_id == refs[0]
    assert created.manager_id is None

    current = await employments.current(TENANT_A, created.id)
    assert current is not None
    assert current.effective_from == HIRED_AT
    assert current.effective_to is None
    assert current.department_id == refs[0]
    assert current.employment_type == EmploymentType.FULL_TIME

    # Auto codes continue per tenant.
    second = await create_employee(
        service,
        refs,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
    )
    assert second.employee_code == "EMP-00002"

    assert [entry.action for entry in audit.entries] == [
        "employee.created",
        "employee.created",
    ]
    entry = audit.entries[0]
    assert entry is not None
    assert entry.entity_type == "employee"
    assert entry.entity_id == created.id
    assert entry.occurred_at == NOW
    assert entry.changes is not None
    assert "first_name" not in entry.changes and "email" not in entry.changes  # no PII


async def test_create_supplied_code_is_kept_and_monotonic_scan_ignores_it(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()

    first = await create_employee(service, refs, employee_code="EMP-00042")
    assert first.employee_code == "EMP-00042"

    second = await create_employee(
        service,
        refs,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
    )
    assert second.employee_code == "EMP-00043"


async def test_create_duplicate_email_and_code(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    await create_employee(service, refs)

    with pytest.raises(EmployeeEmailTakenError):
        await create_employee(service, refs, employee_code="EMP-00002")
    with pytest.raises(EmployeeCodeTakenError):
        await create_employee(
            service, refs, email="other@example.test", employee_code="EMP-00001"
        )


async def test_create_same_email_allowed_in_other_tenant(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs_a = await orgs.seed(TENANT_A)
    refs_b = await orgs.seed(TENANT_B)
    await create_employee(service, refs_a)

    other = await create_employee(service, refs_b, caller=identity(TENANT_B))

    assert other.tenant_id == TENANT_B
    assert other.employee_code == "EMP-00001"


@pytest.mark.parametrize("index", [0, 1, 2])
async def test_create_unknown_reference_raises_org_not_found(
    service: EmployeeService, orgs: OrgWorld, index: int
) -> None:
    refs = list(await orgs.seed())
    refs[index] = uuid7()
    errors = (DepartmentNotFoundError, PositionNotFoundError, LocationNotFoundError)

    with pytest.raises(errors[index]):
        await create_employee(service, tuple(refs))  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["department", "position", "location"])
async def test_create_archived_reference_raises_transfer_target_archived(
    service: EmployeeService, orgs: OrgWorld, kind: str
) -> None:
    refs = await orgs.seed()
    if kind == "department":
        await orgs.department_repo.update(TENANT_A, refs[0], archived_at=ARCHIVED_AT)
    elif kind == "position":
        await orgs.position_repo.update(TENANT_A, refs[1], archived_at=ARCHIVED_AT)
    else:
        await orgs.location_repo.update(TENANT_A, refs[2], archived_at=ARCHIVED_AT)

    with pytest.raises(TransferTargetArchivedError):
        await create_employee(service, refs)


async def test_create_manager_must_be_active_same_tenant_employee(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()

    with pytest.raises(EmployeeManagerInvalidError):
        await create_employee(service, refs, manager_id=uuid7())  # unknown

    manager = await create_employee(service, refs)
    report = await create_employee(
        service, refs, email="report@example.test", manager_id=manager.id
    )
    assert report.manager_id == manager.id

    # A terminated manager is invalid.
    await service.terminate(
        identity(),
        manager.id,
        TerminationRequest(effective_date=TODAY, reason="x"),
        NOW,
    )
    with pytest.raises(EmployeeManagerInvalidError):
        await create_employee(
            service, refs, email="other@example.test", manager_id=manager.id
        )


# --- reads --------------------------------------------------------------------


async def test_get_cross_tenant_and_unknown_are_not_found(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)

    with pytest.raises(EmployeeNotFoundError):
        await service.get(identity(TENANT_B), created.id)
    with pytest.raises(EmployeeNotFoundError):
        await service.get(identity(), uuid7())


async def test_me_resolves_linked_employee_and_missing_is_not_found(
    service: EmployeeService,
    repo: FakeEmployeeRepo,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()
    # The employee linked to the acting user — a separate row from the user id.
    await seed_actor_employee(repo, refs)

    mine = await service.me(identity(employee_id=EMPLOYEE_ID))
    assert mine.id == EMPLOYEE_ID

    # A user without an employee link and a cross-tenant link both 404.
    with pytest.raises(EmployeeNotFoundError):
        await service.me(identity())
    with pytest.raises(EmployeeNotFoundError):
        await service.me(identity(TENANT_B, employee_id=EMPLOYEE_ID))


async def test_detail_returns_current_and_history_newest_first(
    service: EmployeeService,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    await service.transfer(
        identity(),
        created.id,
        TransferRequest(department_id=refs[0], effective_from=LATER),
        NOW,
    )

    detail = await service.get_detail(identity(), created.id)

    assert [period.effective_from for period in detail.employments] == [LATER, HIRED_AT]
    assert detail.current_employment is not None
    assert detail.current_employment.effective_from == LATER
    assert detail.employments[1] is not None
    assert detail.employments[1].effective_to == LATER  # previous period closed


async def test_detail_of_terminated_employee_has_no_current_employment(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    await service.terminate(
        identity(),
        created.id,
        TerminationRequest(effective_date=TODAY, reason="x"),
        NOW,
    )

    detail = await service.get_detail(identity(), created.id)

    assert detail.status == EmployeeStatus.TERMINATED
    assert detail.current_employment is None
    assert detail.terminated_at == TODAY


async def test_manager_reads_only_own_reports(
    service: EmployeeService, repo: FakeEmployeeRepo, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    await seed_actor_employee(repo, refs)  # the manager's employee link
    report = await create_employee(service, refs, manager_id=EMPLOYEE_ID)
    outsider = await create_employee(service, refs, email="outsider@example.test")
    manager = identity(role=Role.MANAGER, employee_id=EMPLOYEE_ID)

    page = await service.list(manager, EmployeeFilters(), PageParams())
    assert [item.id for item in page.items] == [report.id]

    # Detail: own report 200, non-report 404 (never 403 — no existence leak).
    detail = await service.get_detail(manager, report.id)
    assert detail.id == report.id
    with pytest.raises(EmployeeNotFoundError):
        await service.get_detail(manager, outsider.id)
    with pytest.raises(EmployeeNotFoundError):
        await service.list_employments(manager, outsider.id)

    # The forced scope wins even when the manager asks for someone else's reports.
    forced = await service.list(
        manager, EmployeeFilters(manager_id=outsider.id), PageParams()
    )
    assert [item.id for item in forced.items] == [report.id]


async def test_manager_without_employee_link_sees_no_reports(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    target = await create_employee(service, refs)  # manager-less by default
    unlinked = identity(role=Role.MANAGER)

    page = await service.list(unlinked, EmployeeFilters(), PageParams())
    assert page.items == []
    assert page.page.total_items == 0
    # The target exists (`manager_id=None`): the 404 must come from the
    # manager-scoping guard, not the unknown-employee path.
    with pytest.raises(EmployeeNotFoundError):
        await service.get_detail(unlinked, target.id)
    with pytest.raises(EmployeeNotFoundError):
        await service.list_employments(unlinked, target.id)


async def test_list_filters_are_passed_through(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    await create_employee(service, refs)
    await create_employee(
        service,
        refs,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
    )

    page = await service.list(identity(), EmployeeFilters(q="hopper"), PageParams())
    assert page.page.total_items == 1
    assert page.items[0] is not None and page.items[0].last_name == "Hopper"


# --- update -------------------------------------------------------------------


async def test_update_records_changed_field_names_only(
    service: EmployeeService,
    repo: FakeEmployeeRepo,
    audit: RecordingAuditWriter,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    audit.entries.clear()

    updated = await service.update(
        identity(),
        created.id,
        UpdateEmployee(first_name="Augusta", phone="+84-000"),
        NOW,
    )

    assert updated.first_name == "Augusta"
    assert updated.phone == "+84-000"
    entry = audit.entries[-1]
    assert entry is not None
    assert entry.action == "employee.updated"
    assert entry.changes == {"first_name": "changed", "phone": "changed"}
    assert "Augusta" not in str(entry.changes)  # never the values


async def test_update_email_taken_and_case_insensitive(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    await create_employee(service, refs)
    second = await create_employee(
        service,
        refs,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.test",
    )

    with pytest.raises(EmployeeEmailTakenError):
        await service.update(
            identity(), second.id, UpdateEmployee(email="ADA@example.test"), NOW
        )

    # Changing case of one's own email is not a conflict.
    updated = await service.update(
        identity(), second.id, UpdateEmployee(email="GRACE@example.test"), NOW
    )
    assert updated.email == "GRACE@example.test"


async def test_update_empty_patch_is_noop_without_audit(
    service: EmployeeService, audit: RecordingAuditWriter, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    audit.entries.clear()

    updated = await service.update(identity(), created.id, UpdateEmployee(), NOW)

    assert updated.id == created.id
    assert audit.entries == []


async def test_update_terminated_employee_is_allowed(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    await service.terminate(
        identity(),
        created.id,
        TerminationRequest(effective_date=TODAY, reason="x"),
        NOW,
    )

    updated = await service.update(
        identity(), created.id, UpdateEmployee(last_name="Corrected"), NOW
    )

    assert updated.last_name == "Corrected"
    assert updated.status == EmployeeStatus.TERMINATED


async def test_update_phone_can_be_cleared(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    await service.update(identity(), created.id, UpdateEmployee(phone="+84-000"), NOW)

    cleared = await service.update(
        identity(),
        created.id,
        UpdateEmployee.model_validate({"phone": None}),
        NOW,
    )

    assert cleared.phone is None


# --- transfer -----------------------------------------------------------------


async def test_transfer_closes_period_appends_and_inherits(
    service: EmployeeService,
    repo: FakeEmployeeRepo,
    employments: FakeEmploymentRepo,
    audit: RecordingAuditWriter,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    audit.entries.clear()

    target_department = uuid7()
    await orgs.department_repo.add(TENANT_A, target_department, "Ops", None)

    period = await service.transfer(
        identity(),
        created.id,
        TransferRequest(department_id=target_department, effective_from=LATER),
        NOW,
    )

    assert period.department_id == target_department
    assert period.position_id == refs[1]  # inherited
    assert period.location_id == refs[2]  # inherited
    assert period.employment_type == EmploymentType.FULL_TIME
    assert period.effective_to is None

    history = await employments.history(TENANT_A, created.id)
    assert [item.effective_from for item in history] == [LATER, HIRED_AT]
    old = history[1]
    assert old is not None and old.effective_to == LATER  # closed, never rewritten

    refreshed = await repo.get(TENANT_A, created.id)
    assert refreshed is not None
    assert refreshed.department_id == target_department
    assert refreshed.position_id == refs[1]

    entry = audit.entries[-1]
    assert entry is not None and entry.action == "employee.transferred"
    assert entry.changes is not None
    assert entry.changes["department_id"] == {
        "old": str(refs[0]),
        "new": str(target_department),
    }
    assert entry.changes["effective_from"] == {"old": None, "new": LATER.isoformat()}


async def test_transfer_manager_change_and_explicit_null_clears(
    service: EmployeeService, repo: FakeEmployeeRepo, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    manager = await create_employee(service, refs)
    created = await create_employee(
        service, refs, email="report@example.test", manager_id=manager.id
    )

    changed = await service.transfer(
        identity(),
        created.id,
        TransferRequest(department_id=refs[0], manager_id=None, effective_from=LATER),
        NOW,
    )
    assert changed is not None
    refreshed = await repo.get(TENANT_A, created.id)
    assert refreshed is not None and refreshed.manager_id is None


async def test_transfer_self_manager_and_invalid_manager(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)

    with pytest.raises(EmployeeSelfManagerError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(
                department_id=refs[0], manager_id=created.id, effective_from=LATER
            ),
            NOW,
        )
    with pytest.raises(EmployeeManagerInvalidError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(
                department_id=refs[0], manager_id=uuid7(), effective_from=LATER
            ),
            NOW,
        )


async def test_transfer_effective_date_rules(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)

    # Before hired_at → 422
    with pytest.raises(EmployeeTransferEffectiveInvalidError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(department_id=refs[0], effective_from=date(2024, 12, 31)),
            NOW,
        )
    # In the future → 422 (no scheduling in Phase 1)
    with pytest.raises(EmployeeTransferEffectiveInvalidError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(department_id=refs[0], effective_from=date(2026, 12, 31)),
            NOW,
        )

    # Backdating past the current period start → 409 overlap
    await service.transfer(
        identity(),
        created.id,
        TransferRequest(department_id=refs[0], effective_from=LATER),
        NOW,
    )
    with pytest.raises(EmploymentPeriodOverlapError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(department_id=refs[0], effective_from=date(2025, 6, 1)),
            NOW,
        )

    # Same-day repeat is allowed (zero-length closed period, invariant holds).
    again = await service.transfer(
        identity(),
        created.id,
        TransferRequest(department_id=refs[0], effective_from=LATER),
        NOW,
    )
    assert again.effective_from == LATER


async def test_transfer_archived_target_and_unknown_target(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)

    archived = uuid7()
    await orgs.department_repo.add(TENANT_A, archived, "Legacy", None)
    await orgs.department_repo.update(TENANT_A, archived, archived_at=ARCHIVED_AT)

    with pytest.raises(TransferTargetArchivedError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(department_id=archived, effective_from=LATER),
            NOW,
        )
    with pytest.raises(DepartmentNotFoundError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(department_id=uuid7(), effective_from=LATER),
            NOW,
        )


async def test_transfer_terminated_employee_is_rejected(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    await service.terminate(
        identity(),
        created.id,
        TerminationRequest(effective_date=TODAY, reason="x"),
        NOW,
    )

    with pytest.raises(EmployeeAlreadyTerminatedError):
        await service.transfer(
            identity(),
            created.id,
            TransferRequest(department_id=refs[0], effective_from=TODAY),
            NOW,
        )


# --- terminate ----------------------------------------------------------------


async def test_terminate_closes_period_and_audits_reason(
    service: EmployeeService,
    repo: FakeEmployeeRepo,
    employments: FakeEmploymentRepo,
    audit: RecordingAuditWriter,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    audit.entries.clear()

    terminated = await service.terminate(
        identity(),
        created.id,
        TerminationRequest(effective_date=LATER, reason="resigned"),
        NOW,
    )

    assert terminated.status == EmployeeStatus.TERMINATED
    assert terminated.terminated_at == LATER
    assert await employments.current(TENANT_A, created.id) is None

    entry = audit.entries[-1]
    assert entry is not None
    assert entry.action == "employee.terminated"
    assert entry.reason == "resigned"  # reason lives on audit only
    assert entry.changes is not None
    assert "resigned" not in str(entry.changes)


async def test_terminate_clears_direct_reports_with_audit_each(
    service: EmployeeService,
    repo: FakeEmployeeRepo,
    audit: RecordingAuditWriter,
    orgs: OrgWorld,
) -> None:
    refs = await orgs.seed()
    manager = await create_employee(service, refs)
    report = await create_employee(
        service, refs, email="report@example.test", manager_id=manager.id
    )
    audit.entries.clear()

    await service.terminate(
        identity(),
        manager.id,
        TerminationRequest(effective_date=LATER, reason="x"),
        NOW,
    )

    refreshed = await repo.get(TENANT_A, report.id)
    assert refreshed is not None and refreshed.manager_id is None
    cleared = _report_ids(audit.entries)
    assert cleared == [report.id]
    entry = next(e for e in audit.entries if e.action == "employee.manager_cleared")
    assert entry.changes == {"manager_id": {"old": str(manager.id), "new": None}}


async def test_terminate_already_terminated_and_backdating(
    service: EmployeeService, orgs: OrgWorld
) -> None:
    refs = await orgs.seed()
    created = await create_employee(service, refs)
    await service.terminate(
        identity(),
        created.id,
        TerminationRequest(effective_date=LATER, reason="x"),
        NOW,
    )

    with pytest.raises(EmployeeAlreadyTerminatedError):
        await service.terminate(
            identity(),
            created.id,
            TerminationRequest(effective_date=LATER, reason="y"),
            NOW,
        )

    # Backdating before the open period start is rejected (period invariant).
    other = await create_employee(service, refs, email="other@example.test")
    await service.transfer(
        identity(),
        other.id,
        TransferRequest(department_id=refs[0], effective_from=LATER),
        NOW,
    )
    with pytest.raises(EmploymentPeriodOverlapError):
        await service.terminate(
            identity(),
            other.id,
            TerminationRequest(effective_date=date(2025, 6, 1), reason="x"),
            NOW,
        )


async def test_terminate_unknown_employee_is_not_found(
    service: EmployeeService,
) -> None:
    with pytest.raises(EmployeeNotFoundError):
        await service.terminate(
            identity(),
            uuid7(),
            TerminationRequest(effective_date=TODAY, reason="x"),
            NOW,
        )


# --- role scoping on mutations is enforced by policy, not here -----------------
