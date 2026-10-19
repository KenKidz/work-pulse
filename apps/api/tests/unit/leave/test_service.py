"""Unit tests for `LeaveService` — the pinned leave rules (T4).

Every call passes an explicit `NOW`; the service never reads the wall clock
(`.ai/testing.md`: no wall-time assertions). The fake repository enforces the
same tenant scope, overlap, transition and deduction guards as the Postgres
adapter, so these tests exercise business logic, not I/O.
"""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from uuid_utils.compat import uuid7

from app.domains.employees.exceptions import EmployeeNotFoundError
from app.domains.employees.schemas import EmployeeStatus
from app.domains.leave.exceptions import (
    EmployeeNotActiveError,
    LeaveInsufficientBalanceError,
    LeaveNotPendingError,
    LeaveOverlapsError,
    LeaveRequestNotFoundError,
    LeaveStartTooOldError,
    LeaveTypeNotFoundError,
)
from app.domains.leave.schemas import LeaveDecision, LeaveRequestStatus
from app.domains.leave.service import LeaveService
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.leave.conftest import (
    ANNUAL,
    EMPLOYEE,
    MANAGER,
    NOW,
    OTHER,
    REPORT,
    SICK,
    TENANT_A,
    TENANT_B,
    TODAY,
    UNPAID,
    USER,
    decider,
    identity,
    seed_employee,
    seed_types,
    submit,
)
from tests.unit.leave.fakes import FakeLeaveRepo
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox

PENDING = LeaveRequestStatus.PENDING
APPROVED = LeaveRequestStatus.APPROVED
REJECTED = LeaveRequestStatus.REJECTED
WITHDRAWN = LeaveRequestStatus.WITHDRAWN


async def test_submit_creates_a_pending_request_with_inclusive_days(
    service: LeaveService,
    leave_repo: FakeLeaveRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)

    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 10, 19),
        end_date=date(2026, 10, 23),
        reason="Family trip",
    )

    assert request.status is PENDING
    assert request.employee_id == EMPLOYEE
    assert request.leave_type_code is ANNUAL
    assert request.start_date == date(2026, 10, 19)
    assert request.end_date == date(2026, 10, 23)
    assert request.days == 5  # inclusive whole calendar days

    balance = await leave_repo.get_or_create_balance(
        TENANT_A, EMPLOYEE, types[ANNUAL].id, 2026
    )
    assert balance is not None
    assert (balance.entitlement_days, balance.used_days) == (12, 0)

    assert [envelope["event_type"] for envelope in outbox.envelopes] == [
        "leave.requested"
    ]
    envelope = outbox.envelopes[0]
    assert envelope["version"] == 1
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "leave_request_id": str(request.id),
        "employee_id": str(EMPLOYEE),
        "leave_type_code": "annual",
        "start_date": "2026-10-19",
        "end_date": "2026-10-23",
        "days": 5,
    }
    assert [entry.action for entry in audit.entries] == ["leave.requested"]
    changes = audit.entries[0].changes
    assert changes is not None
    assert changes["status"] == {"old": None, "new": "pending"}
    assert changes["reason"] == "changed"  # PII: field marker only
    assert "Family trip" not in json.dumps(changes)
    assert "Family trip" not in json.dumps(outbox.envelopes)


async def test_overlapping_pending_and_approved_ranges_are_rejected(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo, employee_id=REPORT)
    types = await seed_types(leave_repo)
    requester = identity(employee_id=REPORT)
    approver = decider()

    first = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=requester,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 22),
    )
    # Touching the pending range's last day is an overlap (inclusive).
    with pytest.raises(LeaveOverlapsError):
        await submit(
            service,
            leave_type_id=types[ANNUAL].id,
            caller=requester,
            start_date=date(2026, 10, 22),
            end_date=date(2026, 10, 23),
        )
    await service.approve(approver, first.id, LeaveDecision(), NOW)
    with pytest.raises(LeaveOverlapsError):
        await submit(
            service,
            leave_type_id=types[SICK].id,
            caller=requester,
            start_date=date(2026, 10, 19),
            end_date=date(2026, 10, 20),
        )

    # Withdrawn and rejected ranges never block.
    second = await submit(
        service,
        leave_type_id=types[SICK].id,
        caller=requester,
        start_date=date(2026, 10, 26),
        end_date=date(2026, 10, 28),
    )
    await service.withdraw(requester, second.id, NOW)
    third = await submit(
        service,
        leave_type_id=types[SICK].id,
        caller=requester,
        start_date=date(2026, 10, 26),
        end_date=date(2026, 10, 28),
    )
    assert third.status is PENDING

    fourth = await submit(
        service,
        leave_type_id=types[SICK].id,
        caller=requester,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 4),
    )
    await service.reject(approver, fourth.id, LeaveDecision(), NOW)
    fifth = await submit(
        service,
        leave_type_id=types[SICK].id,
        caller=requester,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 4),
    )
    assert fifth.status is PENDING


async def test_submit_serializes_the_overlap_check_behind_the_employee_lock(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    """The row lock is taken before the overlap read (the race backstop)."""
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)

    await submit(service, leave_type_id=types[ANNUAL].id)

    assert leave_repo.calls.index("lock_employee") < leave_repo.calls.index(
        "find_overlapping"
    )


async def test_start_date_rules_use_the_injected_clock(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    yesterday = TODAY - timedelta(days=1)

    with pytest.raises(LeaveStartTooOldError):
        await submit(
            service,
            leave_type_id=types[ANNUAL].id,
            start_date=yesterday,
            end_date=yesterday,
        )
    with pytest.raises(LeaveStartTooOldError):
        await submit(
            service,
            leave_type_id=types[UNPAID].id,
            start_date=yesterday,
            end_date=yesterday,
        )

    # Today is the annual/unpaid boundary; sick reaches exactly 7 days back.
    today_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=TODAY,
        end_date=TODAY,
    )
    assert today_request.status is PENDING
    sick_start = TODAY - timedelta(days=7)
    sick = await submit(
        service,
        leave_type_id=types[SICK].id,
        start_date=sick_start,
        end_date=sick_start,
    )
    assert sick.status is PENDING
    with pytest.raises(LeaveStartTooOldError):
        await submit(
            service,
            leave_type_id=types[SICK].id,
            start_date=sick_start - timedelta(days=1),
            end_date=sick_start - timedelta(days=1),
        )


async def test_submit_checks_the_balance_and_unpaid_bypasses(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    start = date(2026, 11, 2)

    with pytest.raises(LeaveInsufficientBalanceError):
        await submit(
            service,
            leave_type_id=types[ANNUAL].id,
            start_date=start,
            end_date=start + timedelta(days=12),  # 13 days > 12
        )

    unpaid = await submit(
        service,
        leave_type_id=types[UNPAID].id,
        start_date=start,
        end_date=start + timedelta(days=12),
    )
    assert unpaid.days == 13
    assert all(key[2] != types[UNPAID].id for key in leave_repo.balances), (
        "unpaid must never create or touch a balance"
    )

    # Exactly the entitlement is allowed; once approved the balance is spent.
    annual = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 20),
        end_date=date(2026, 12, 1),  # 12 days
    )
    assert annual.days == 12
    await service.approve(decider(), annual.id, LeaveDecision(), NOW)
    with pytest.raises(LeaveInsufficientBalanceError):
        await submit(
            service,
            leave_type_id=types[ANNUAL].id,
            start_date=date(2026, 12, 3),
            end_date=date(2026, 12, 3),
        )


async def test_approve_deducts_the_balance_and_emits_the_fact(
    service: LeaveService,
    leave_repo: FakeLeaveRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(leave_repo, employee_id=REPORT)
    types = await seed_types(leave_repo)
    request = await submit(
        service,
        leave_type_id=types[SICK].id,
        caller=identity(employee_id=REPORT),
        start_date=TODAY - timedelta(days=2),
        end_date=TODAY,
        reason="Flu",
    )
    outbox.envelopes.clear()
    audit.entries.clear()

    approved = await service.approve(
        decider(), request.id, LeaveDecision(decision_note="Get well"), NOW
    )

    assert approved.status is APPROVED
    assert approved.decided_by == USER
    assert approved.decided_at == NOW
    assert approved.decision_note == "Get well"
    balance = await leave_repo.get_or_create_balance(
        TENANT_A, REPORT, types[SICK].id, 2026
    )
    assert balance is not None
    assert (balance.used_days, balance.remaining_days) == (3, 3)

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "leave.approved"
    assert envelope["data"]["decided_by"] == str(USER)
    assert envelope["data"]["days"] == 3
    assert "Get well" not in json.dumps(envelope)
    [entry] = audit.entries
    assert entry.action == "leave.approved"
    assert entry.changes == {
        "status": {"old": "pending", "new": "approved"},
        "decided_by": {"old": None, "new": str(USER)},
        "decision_note": "changed",
    }
    assert "Get well" not in json.dumps(entry.changes)


async def test_reject_leaves_the_balance_untouched(
    service: LeaveService,
    leave_repo: FakeLeaveRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 5),
    )
    outbox.envelopes.clear()
    audit.entries.clear()

    rejected = await service.reject(decider(), request.id, LeaveDecision(), NOW)

    assert rejected.status is REJECTED
    balance = await leave_repo.get_or_create_balance(
        TENANT_A, EMPLOYEE, types[ANNUAL].id, 2026
    )
    assert balance is not None and balance.used_days == 0
    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "leave.rejected"
    assert envelope["data"]["decided_by"] == str(USER)
    [entry] = audit.entries
    assert entry.action == "leave.rejected"
    assert entry.changes is not None
    assert "decision_note" not in entry.changes


async def test_withdraw_is_own_and_pending_only(
    service: LeaveService, leave_repo: FakeLeaveRepo, outbox: RecordingEventOutbox
) -> None:
    seed_employee(leave_repo)
    seed_employee(leave_repo, employee_id=MANAGER)
    seed_employee(leave_repo, employee_id=OTHER)
    types = await seed_types(leave_repo)

    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    outbox.envelopes.clear()
    withdrawn = await service.withdraw(identity(), request.id, NOW)

    assert withdrawn.status is WITHDRAWN
    assert withdrawn.decided_by is None and withdrawn.decided_at is None
    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "leave.withdrawn"
    assert envelope["data"]["employee_id"] == str(EMPLOYEE)

    with pytest.raises(LeaveNotPendingError):
        await service.withdraw(identity(), request.id, NOW)

    other_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=OTHER),
        start_date=date(2026, 11, 9),
        end_date=date(2026, 11, 10),
    )
    # Visible standing but not the owner is a forbidden transition.
    with pytest.raises(ForbiddenError):
        await service.withdraw(
            identity(role=Role.HR, employee_id=MANAGER), other_request.id, NOW
        )
    # An out-of-scope viewer must not learn the request exists.
    with pytest.raises(LeaveRequestNotFoundError):
        await service.withdraw(identity(employee_id=REPORT), other_request.id, NOW)
    with pytest.raises(LeaveRequestNotFoundError):
        await service.withdraw(identity(tenant_id=TENANT_B), request.id, NOW)


async def test_second_decision_cannot_apply_twice(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 6),
    )
    approver = decider()

    await service.approve(approver, request.id, LeaveDecision(), NOW)
    with pytest.raises(LeaveNotPendingError):
        await service.approve(approver, request.id, LeaveDecision(), NOW)
    with pytest.raises(LeaveNotPendingError):
        await service.reject(approver, request.id, LeaveDecision(), NOW)

    balance = await leave_repo.get_or_create_balance(
        TENANT_A, EMPLOYEE, types[ANNUAL].id, 2026
    )
    assert balance is not None and balance.used_days == 5


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
async def test_self_approval_is_forbidden_for_every_decider_role(
    role: Role, service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    seed_employee(leave_repo, employee_id=MANAGER)
    types = await seed_types(leave_repo)
    me = identity(role=role, employee_id=EMPLOYEE)
    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=me,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )

    with pytest.raises(ForbiddenError):
        await service.approve(me, request.id, LeaveDecision(), NOW)
    with pytest.raises(ForbiddenError):
        await service.reject(me, request.id, LeaveDecision(), NOW)

    balance = await leave_repo.get_or_create_balance(
        TENANT_A, EMPLOYEE, types[ANNUAL].id, 2026
    )
    assert balance is not None and balance.used_days == 0


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
async def test_employee_and_finance_cannot_decide(
    role: Role, service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    caller = identity(role=role, employee_id=REPORT)

    with pytest.raises(ForbiddenError):
        await service.approve(caller, request.id, LeaveDecision(), NOW)
    with pytest.raises(ForbiddenError):
        await service.reject(caller, request.id, LeaveDecision(), NOW)


async def test_manager_decides_only_direct_reports(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo, employee_id=MANAGER)
    seed_employee(leave_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(leave_repo, employee_id=OTHER)
    types = await seed_types(leave_repo)

    report_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=REPORT),
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    other_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=OTHER),
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    manager = identity(role=Role.MANAGER, employee_id=MANAGER)

    approved = await service.approve(manager, report_request.id, LeaveDecision(), NOW)
    assert approved.status is APPROVED
    with pytest.raises(LeaveRequestNotFoundError):
        await service.approve(manager, other_request.id, LeaveDecision(), NOW)


async def test_approve_rechecks_the_balance_after_earlier_approvals(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    first = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 7),  # 6 days
    )
    second = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 10),
        end_date=date(2026, 11, 16),  # 7 days; valid at submit (balance 12)
    )
    approver = decider()

    await service.approve(approver, first.id, LeaveDecision(), NOW)
    with pytest.raises(LeaveInsufficientBalanceError):
        await service.approve(approver, second.id, LeaveDecision(), NOW)

    pending = await leave_repo.get_request(TENANT_A, second.id)
    assert pending is not None and pending.status is PENDING
    balance = await leave_repo.get_or_create_balance(
        TENANT_A, EMPLOYEE, types[ANNUAL].id, 2026
    )
    assert balance is not None and balance.used_days == 6


async def test_year_boundary_deducts_from_the_start_year(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    now = datetime(2026, 12, 28, 12, 0, tzinfo=UTC)

    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 12, 28),
        end_date=date(2027, 1, 2),  # 6 whole calendar days
        now=now,
    )
    assert request.days == 6
    await service.approve(decider(), request.id, LeaveDecision(), now)

    balance_2026 = leave_repo.balances[(TENANT_A, EMPLOYEE, types[ANNUAL].id, 2026)]
    assert balance_2026.used_days == 6
    assert (TENANT_A, EMPLOYEE, types[ANNUAL].id, 2027) not in leave_repo.balances


async def test_unpaid_never_touches_a_balance(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    request = await submit(
        service,
        leave_type_id=types[UNPAID].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 14),  # 13 days, more than any entitlement
    )

    await service.approve(decider(), request.id, LeaveDecision(), NOW)

    assert all(key[2] != types[UNPAID].id for key in leave_repo.balances)
    assert await leave_repo.list_balances(TENANT_A, employee_ids=[EMPLOYEE]) == []


async def test_submit_requires_an_active_linked_employee(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    types = await seed_types(leave_repo)

    with pytest.raises(EmployeeNotFoundError):
        await submit(
            service,
            leave_type_id=types[ANNUAL].id,
            caller=identity(employee_id=None),
        )
    with pytest.raises(EmployeeNotFoundError):
        await submit(
            service,
            leave_type_id=types[ANNUAL].id,
            caller=identity(employee_id=OTHER),
        )

    seed_employee(leave_repo, status=EmployeeStatus.TERMINATED)
    with pytest.raises(EmployeeNotActiveError):
        await submit(service, leave_type_id=types[ANNUAL].id)

    seed_employee(leave_repo)  # active again
    with pytest.raises(LeaveTypeNotFoundError):
        await submit(service, leave_type_id=uuid7())


async def test_list_requests_scopes_by_role(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    seed_employee(leave_repo, employee_id=MANAGER)
    seed_employee(leave_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(leave_repo, employee_id=OTHER)
    types = await seed_types(leave_repo)

    own = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(),
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    manager_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=MANAGER),
        start_date=date(2026, 11, 4),
        end_date=date(2026, 11, 5),
    )
    report_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=REPORT),
        start_date=date(2026, 11, 6),
        end_date=date(2026, 11, 7),
    )
    await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=OTHER),
        start_date=date(2026, 11, 8),
        end_date=date(2026, 11, 9),
    )

    own_page = await service.list_requests(identity(), page=PageParams())
    assert [item.id for item in own_page.items] == [own.id]
    assert own_page.page.total_items == 1

    manager_page = await service.list_requests(
        identity(role=Role.MANAGER, employee_id=MANAGER), page=PageParams()
    )
    assert {item.id for item in manager_page.items} == {
        manager_request.id,
        report_request.id,
    }

    hr_page = await service.list_requests(
        identity(role=Role.HR, employee_id=MANAGER), page=PageParams()
    )
    assert hr_page.page.total_items == 4

    filtered = await service.list_requests(
        identity(role=Role.HR, employee_id=MANAGER),
        employee_id=REPORT,
        page=PageParams(),
    )
    assert [item.id for item in filtered.items] == [report_request.id]

    with pytest.raises(EmployeeNotFoundError):
        await service.list_requests(identity(), employee_id=REPORT, page=PageParams())
    with pytest.raises(EmployeeNotFoundError):
        await service.list_requests(
            identity(role=Role.MANAGER, employee_id=MANAGER),
            employee_id=OTHER,
            page=PageParams(),
        )

    cross_tenant = await service.list_requests(
        identity(tenant_id=TENANT_B), page=PageParams()
    )
    assert cross_tenant.items == [] and cross_tenant.page.total_items == 0


async def test_get_request_never_leaks_out_of_scope_ids(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    seed_employee(leave_repo, employee_id=MANAGER)
    seed_employee(leave_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(leave_repo, employee_id=OTHER)
    types = await seed_types(leave_repo)

    own = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(),
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    report_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=REPORT),
        start_date=date(2026, 11, 4),
        end_date=date(2026, 11, 5),
    )
    other_request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=OTHER),
        start_date=date(2026, 11, 6),
        end_date=date(2026, 11, 7),
    )

    assert (await service.get_request(identity(), own.id)).id == own.id
    with pytest.raises(LeaveRequestNotFoundError):
        await service.get_request(identity(), report_request.id)

    manager = identity(role=Role.MANAGER, employee_id=MANAGER)
    assert (
        await service.get_request(manager, report_request.id)
    ).id == report_request.id
    with pytest.raises(LeaveRequestNotFoundError):
        await service.get_request(manager, other_request.id)

    hr = identity(role=Role.HR, employee_id=MANAGER)
    assert (await service.get_request(hr, other_request.id)).id == other_request.id
    with pytest.raises(LeaveRequestNotFoundError):
        await service.get_request(identity(tenant_id=TENANT_B), own.id)


async def test_list_types_provisions_the_seeded_types_per_tenant(
    service: LeaveService,
) -> None:
    rows = await service.list_types(identity())
    assert [row.code for row in rows] == [ANNUAL, SICK, UNPAID]

    again = await service.list_types(identity())
    assert [row.id for row in again] == [row.id for row in rows]

    other = await service.list_types(identity(tenant_id=TENANT_B))
    assert {row.id for row in other}.isdisjoint({row.id for row in rows})


async def test_list_balances_scopes_and_filters_by_year(
    service: LeaveService, leave_repo: FakeLeaveRepo
) -> None:
    seed_employee(leave_repo)
    seed_employee(leave_repo, employee_id=MANAGER)
    seed_employee(leave_repo, employee_id=REPORT, manager_id=MANAGER)
    types = await seed_types(leave_repo)

    await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(),
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        caller=identity(employee_id=REPORT),
        start_date=date(2027, 1, 5),
        end_date=date(2027, 1, 6),
    )

    own = await service.list_balances(identity())
    assert [(row.employee_id, row.year) for row in own] == [(EMPLOYEE, 2026)]
    assert await service.list_balances(identity(), year=2027) == []

    manager = identity(role=Role.MANAGER, employee_id=MANAGER)
    report_balances = await service.list_balances(manager)
    assert [(row.employee_id, row.year) for row in report_balances] == [(REPORT, 2027)]

    hr = identity(role=Role.HR, employee_id=MANAGER)
    assert len(await service.list_balances(hr)) == 2
    assert len(await service.list_balances(hr, year=2026)) == 1

    with pytest.raises(EmployeeNotFoundError):
        await service.list_balances(identity(), employee_id=REPORT)
    assert await service.list_balances(identity(tenant_id=TENANT_B)) == []


async def test_pii_markers_appear_only_when_text_was_provided(
    service: LeaveService,
    leave_repo: FakeLeaveRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(leave_repo)
    types = await seed_types(leave_repo)
    request = await submit(
        service,
        leave_type_id=types[ANNUAL].id,
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 3),
    )
    assert audit.entries[0].changes is not None
    assert "reason" not in audit.entries[0].changes

    await service.reject(decider(), request.id, LeaveDecision(), NOW)
    assert audit.entries[-1].changes is not None
    assert "decision_note" not in audit.entries[-1].changes
    assert "decision_note" not in json.dumps(outbox.envelopes)
    assert "reason" not in json.dumps(outbox.envelopes)
