"""Unit tests for `OvertimeService` — the pinned overtime rules (T4).

Every call passes an explicit `NOW`; the service never reads the wall clock
(`.ai/testing.md`: no wall-time assertions). The fake repository enforces the
same tenant scope, overlap rule and conditional-transition guards as the
Postgres adapter, so these tests exercise business logic, not I/O.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from uuid_utils.compat import uuid7

from app.domains.employees.schemas import EmployeeStatus
from app.domains.overtime.exceptions import (
    EmployeeNotActiveError,
    EmployeeNotFoundError,
    OvertimeNotManagerApprovedError,
    OvertimeNotOpenError,
    OvertimeNotRequestedError,
    OvertimeOverlapsError,
    OvertimeRequestNotFoundError,
    OvertimeWorkDateInFutureError,
)
from app.domains.overtime.schemas import (
    OvertimeDecision,
    OvertimeRejectedFrom,
    OvertimeRequestCreate,
    OvertimeRequestStatus,
)
from app.domains.overtime.service import OvertimeService
from app.shared.errors import ForbiddenError
from app.shared.identity import Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.overtime.conftest import (
    EMPLOYEE,
    MANAGER,
    NOW,
    OTHER,
    REPORT,
    TENANT_A,
    TENANT_B,
    TODAY,
    USER,
    approve,
    decider,
    identity,
    manager,
    reject,
    seed_employee,
    seed_request,
    submit,
    verify,
)
from tests.unit.overtime.fakes import FakeOvertimeRepo

REQUESTED = OvertimeRequestStatus.REQUESTED
MANAGER_APPROVED = OvertimeRequestStatus.MANAGER_APPROVED
HR_VERIFIED = OvertimeRequestStatus.HR_VERIFIED
INCLUDED = OvertimeRequestStatus.INCLUDED_IN_PAYROLL
REJECTED = OvertimeRequestStatus.REJECTED
WITHDRAWN = OvertimeRequestStatus.WITHDRAWN


async def test_submit_creates_a_requested_claim_with_event_and_audit(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo)
    work_date = date(2026, 10, 18)

    request = await submit(
        service,
        work_date=work_date,
        hours=Decimal("2.50"),
        reason="Late deployment",
    )

    assert request.status is REQUESTED
    assert request.employee_id == EMPLOYEE
    assert request.work_date == work_date
    assert request.hours == Decimal("2.50")
    assert request.reason == "Late deployment"
    assert request.approved_by is None and request.verified_by is None

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "overtime.requested"
    assert envelope["version"] == 1
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "overtime_id": str(request.id),
        "employee_id": str(EMPLOYEE),
        "work_date": "2026-10-18",
        "hours": 2.5,
    }
    assert "Late deployment" not in json.dumps(envelope)

    [entry] = audit.entries
    assert entry.action == "overtime.requested"
    assert entry.entity_type == "overtime_request"
    assert entry.entity_id == request.id
    assert entry.changes is not None
    assert entry.changes["status"] == {"old": None, "new": "requested"}
    assert entry.changes["reason"] == "changed"  # PII: field marker only
    assert "Late deployment" not in json.dumps(entry.changes)


async def test_submit_requires_an_active_linked_employee(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    with pytest.raises(EmployeeNotFoundError):
        await submit(service, caller=identity(employee_id=None))
    with pytest.raises(EmployeeNotFoundError):
        await submit(service, caller=identity(employee_id=OTHER))

    seed_employee(overtime_repo, status=EmployeeStatus.TERMINATED)
    with pytest.raises(EmployeeNotActiveError):
        await submit(service)


async def test_work_date_must_not_be_in_the_future(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)

    with pytest.raises(OvertimeWorkDateInFutureError):
        await submit(service, work_date=date(2026, 10, 20))

    # Today is the boundary; a past claim is allowed.
    today_request = await submit(service, work_date=TODAY)
    assert today_request.status is REQUESTED
    past_request = await submit(service, work_date=date(2026, 10, 5))
    assert past_request.status is REQUESTED


async def test_submit_locks_the_employee_before_the_overlap_check(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    """The row lock is taken before the overlap read (the race backstop)."""
    seed_employee(overtime_repo)

    await submit(service)

    assert overtime_repo.calls.index("lock_employee") < overtime_repo.calls.index(
        "find_open_overlap"
    )


async def test_open_requests_block_a_same_date_claim(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)

    day_one = date(2026, 10, 12)
    first = await submit(service, work_date=day_one)
    with pytest.raises(OvertimeOverlapsError):
        await submit(service, work_date=day_one)
    await service.withdraw(identity(), first.id, NOW)
    # Withdrawn rows never block.
    await submit(service, work_date=day_one)

    day_two = date(2026, 10, 13)
    second = await submit(service, work_date=day_two)
    await approve(service, second.id)
    with pytest.raises(OvertimeOverlapsError):
        await submit(service, work_date=day_two)
    await reject(service, second.id)  # from manager_approved, HR step
    await submit(service, work_date=day_two)

    day_three = date(2026, 10, 14)
    third = await submit(service, work_date=day_three)
    await approve(service, third.id)
    await verify(service, third.id)
    with pytest.raises(OvertimeOverlapsError):
        await submit(service, work_date=day_three)

    # Included rows are counted claims: a later additional claim is allowed.
    day_four = date(2026, 10, 15)
    seed_request(
        overtime_repo,
        work_date=day_four,
        status=INCLUDED,
        included_in_run_id=uuid7(),
    )
    additional = await submit(service, work_date=day_four)
    assert additional.status is REQUESTED


async def test_approve_moves_to_manager_approved_with_event_and_audit(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo, employee_id=REPORT)
    request = await submit(
        service,
        caller=identity(employee_id=REPORT),
        work_date=date(2026, 10, 12),
    )
    audit.entries.clear()
    outbox.envelopes.clear()

    approved = await service.approve(
        decider(), request.id, OvertimeDecision(note="Confirmed with the team"), NOW
    )

    assert approved.status is MANAGER_APPROVED
    assert approved.approved_by == USER
    assert approved.approved_at == NOW
    assert approved.manager_note == "Confirmed with the team"

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "overtime.approved"
    assert envelope["data"] == {
        "overtime_id": str(request.id),
        "employee_id": str(REPORT),
        "work_date": "2026-10-12",
        "hours": 2.5,
        "approved_by": str(USER),
    }
    assert "Confirmed with the team" not in json.dumps(envelope)

    [entry] = audit.entries
    assert entry.action == "overtime.approved"
    assert entry.changes == {
        "status": {"old": "requested", "new": "manager_approved"},
        "approved_by": {"old": None, "new": str(USER)},
        "manager_note": "changed",
    }
    assert "Confirmed with the team" not in json.dumps(entry.changes)


async def test_verify_moves_to_hr_verified_with_event_and_audit(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo, employee_id=REPORT)
    request = await submit(
        service,
        caller=identity(employee_id=REPORT),
        work_date=date(2026, 10, 12),
    )
    await approve(service, request.id, note="Manager note")
    audit.entries.clear()
    outbox.envelopes.clear()

    verified = await service.verify(
        decider(), request.id, OvertimeDecision(note="Hours match the log"), NOW
    )

    assert verified.status is HR_VERIFIED
    assert verified.verified_by == USER
    assert verified.verified_at == NOW
    assert verified.hr_note == "Hours match the log"
    assert verified.manager_note == "Manager note"

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "overtime.verified"
    assert envelope["data"] == {
        "overtime_id": str(request.id),
        "employee_id": str(REPORT),
        "work_date": "2026-10-12",
        "hours": 2.5,
        "verified_by": str(USER),
    }
    assert "Hours match the log" not in json.dumps(envelope)

    [entry] = audit.entries
    assert entry.action == "overtime.verified"
    assert entry.changes == {
        "status": {"old": "manager_approved", "new": "hr_verified"},
        "verified_by": {"old": None, "new": str(USER)},
        "hr_note": "changed",
    }
    assert "Hours match the log" not in json.dumps(entry.changes)


async def test_wrong_state_transitions_raise_the_pinned_codes(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)
    request = await submit(service, work_date=date(2026, 10, 12))

    with pytest.raises(OvertimeNotManagerApprovedError):
        await verify(service, request.id)

    await approve(service, request.id)
    with pytest.raises(OvertimeNotRequestedError):
        await approve(service, request.id)

    await verify(service, request.id)
    with pytest.raises(OvertimeNotManagerApprovedError):
        await verify(service, request.id)
    with pytest.raises(OvertimeNotRequestedError):
        await approve(service, request.id)


async def test_reject_from_each_open_state_records_rejected_from_and_note_route(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(overtime_repo, employee_id=MANAGER)
    requester = identity(employee_id=REPORT)

    # From requested: manager-step gate, note → manager_note.
    first = await submit(service, caller=requester, work_date=date(2026, 10, 12))
    audit.entries.clear()
    outbox.envelopes.clear()
    rejected_one = await reject(
        service, first.id, caller=manager(), note="Not approved by the team"
    )
    assert rejected_one.status is REJECTED
    assert rejected_one.rejected_by == USER
    assert rejected_one.rejected_at == NOW
    assert rejected_one.rejected_from is OvertimeRejectedFrom.REQUESTED
    assert rejected_one.manager_note == "Not approved by the team"
    assert rejected_one.hr_note is None
    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "overtime.rejected"
    assert envelope["data"]["rejected_from"] == "requested"
    assert "Not approved by the team" not in json.dumps(envelope)
    [entry] = audit.entries
    assert entry.action == "overtime.rejected"
    assert entry.changes is not None
    assert entry.changes["status"] == {"old": "requested", "new": "rejected"}
    assert entry.changes["manager_note"] == "changed"
    assert "hr_note" not in entry.changes

    # From manager_approved: HR-step gate, note → hr_note.
    second = await submit(service, caller=requester, work_date=date(2026, 10, 13))
    await approve(service, second.id, caller=manager())
    audit.entries.clear()
    outbox.envelopes.clear()
    rejected_two = await reject(service, second.id, note="Timesheet mismatch")
    assert rejected_two.rejected_from is OvertimeRejectedFrom.MANAGER_APPROVED
    assert rejected_two.hr_note == "Timesheet mismatch"
    [entry] = audit.entries
    assert entry.changes is not None
    assert entry.changes["status"] == {"old": "manager_approved", "new": "rejected"}
    assert entry.changes["hr_note"] == "changed"

    # From hr_verified: the ADR 0018 un-verify window; the manager note survives.
    third = await submit(service, caller=requester, work_date=date(2026, 10, 14))
    await approve(service, third.id, caller=manager(), note="Manager kept this")
    await verify(service, third.id, note="Verified once")
    audit.entries.clear()
    outbox.envelopes.clear()
    rejected_three = await reject(service, third.id, note="Verification revoked")
    assert rejected_three.rejected_from is OvertimeRejectedFrom.HR_VERIFIED
    assert rejected_three.hr_note == "Verification revoked"
    assert rejected_three.manager_note == "Manager kept this"
    [envelope] = outbox.envelopes
    assert envelope["data"]["rejected_from"] == "hr_verified"


async def test_reject_on_terminal_rows_is_not_open(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)

    for status in (REJECTED, WITHDRAWN, INCLUDED):
        row = seed_request(overtime_repo, status=status)
        with pytest.raises(OvertimeNotOpenError):
            await reject(service, row.id)


async def test_withdraw_is_own_and_pre_verification_only(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo)
    seed_employee(overtime_repo, employee_id=MANAGER)
    seed_employee(overtime_repo, employee_id=OTHER)

    first = await submit(service, work_date=date(2026, 10, 12))
    audit.entries.clear()
    outbox.envelopes.clear()
    withdrawn = await service.withdraw(identity(), first.id, NOW)
    assert withdrawn.status is WITHDRAWN
    assert withdrawn.withdrawn_at == NOW
    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "overtime.withdrawn"
    assert envelope["data"] == {
        "overtime_id": str(first.id),
        "employee_id": str(EMPLOYEE),
        "work_date": "2026-10-12",
    }
    [entry] = audit.entries
    assert entry.action == "overtime.withdrawn"
    assert entry.changes == {
        "status": {"old": "requested", "new": "withdrawn"},
    }
    with pytest.raises(OvertimeNotOpenError):
        await service.withdraw(identity(), first.id, NOW)

    second = await submit(service, work_date=date(2026, 10, 13))
    await approve(service, second.id)
    assert (await service.withdraw(identity(), second.id, NOW)).status is WITHDRAWN

    third = await submit(service, work_date=date(2026, 10, 14))
    await approve(service, third.id)
    await verify(service, third.id)
    # HR_VERIFIED is payroll-bound — withdrawal is pre-verification only.
    with pytest.raises(OvertimeNotOpenError):
        await service.withdraw(identity(), third.id, NOW)

    other_request = await submit(
        service,
        caller=identity(employee_id=OTHER),
        work_date=date(2026, 10, 15),
    )
    # Visible standing but not the owner is a forbidden transition.
    with pytest.raises(ForbiddenError):
        await service.withdraw(
            identity(role=Role.HR, employee_id=MANAGER), other_request.id, NOW
        )
    # An out-of-scope viewer must not learn the request exists.
    with pytest.raises(OvertimeRequestNotFoundError):
        await service.withdraw(identity(employee_id=REPORT), other_request.id, NOW)
    with pytest.raises(OvertimeRequestNotFoundError):
        await service.withdraw(identity(tenant_id=TENANT_B), other_request.id, NOW)


@pytest.mark.parametrize("role", [Role.MANAGER, Role.HR, Role.ADMIN, Role.SUPER_ADMIN])
async def test_self_decisions_are_forbidden_for_every_decider_role(
    role: Role, service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)
    seed_employee(overtime_repo, employee_id=MANAGER)
    me = identity(role=role, employee_id=EMPLOYEE)
    request = await submit(
        service,
        caller=me,
        work_date=date(2026, 10, 12),
    )

    with pytest.raises(ForbiddenError):
        await service.approve(me, request.id, OvertimeDecision(), NOW)
    with pytest.raises(ForbiddenError):
        await service.reject(me, request.id, OvertimeDecision(), NOW)

    # Move it past the manager step with an independent decider.
    await approve(service, request.id)
    with pytest.raises(ForbiddenError):
        await service.verify(me, request.id, OvertimeDecision(), NOW)


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.FINANCE])
async def test_employee_and_finance_cannot_decide(
    role: Role, service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)
    request = await submit(service, work_date=date(2026, 10, 12))
    caller = identity(role=role, employee_id=REPORT)

    with pytest.raises(ForbiddenError):
        await service.approve(caller, request.id, OvertimeDecision(), NOW)
    with pytest.raises(ForbiddenError):
        await service.reject(caller, request.id, OvertimeDecision(), NOW)


async def test_manager_cannot_verify_or_reject_after_approval(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo, employee_id=MANAGER)
    seed_employee(overtime_repo, employee_id=REPORT, manager_id=MANAGER)
    caller = manager()
    request = await submit(
        service,
        caller=identity(employee_id=REPORT),
        work_date=date(2026, 10, 12),
    )
    await service.approve(caller, request.id, OvertimeDecision(), NOW)

    # HR step needs an HR role; a manager without one is denied.
    with pytest.raises(ForbiddenError):
        await service.verify(caller, request.id, OvertimeDecision(), NOW)
    with pytest.raises(ForbiddenError):
        await service.reject(caller, request.id, OvertimeDecision(), NOW)


async def test_manager_decides_only_direct_reports(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo, employee_id=MANAGER)
    seed_employee(overtime_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(overtime_repo, employee_id=OTHER)

    report_request = await submit(
        service, caller=identity(employee_id=REPORT), work_date=date(2026, 10, 12)
    )
    other_request = await submit(
        service, caller=identity(employee_id=OTHER), work_date=date(2026, 10, 12)
    )
    caller = manager()

    approved = await service.approve(caller, report_request.id, OvertimeDecision(), NOW)
    assert approved.status is MANAGER_APPROVED
    with pytest.raises(OvertimeRequestNotFoundError):
        await service.approve(caller, other_request.id, OvertimeDecision(), NOW)


async def test_lost_conditional_updates_raise_the_wrong_state_code(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo)
    seed_employee(overtime_repo, employee_id=MANAGER)
    request = await submit(service, work_date=date(2026, 10, 12))
    audit.entries.clear()
    outbox.envelopes.clear()

    overtime_repo.lose_next_transition = True
    with pytest.raises(OvertimeNotRequestedError):
        await service.approve(decider(), request.id, OvertimeDecision(), NOW)
    stored = await overtime_repo.get_request(TENANT_A, request.id)
    assert stored is not None and stored.status is REQUESTED
    # The loser appends neither an event nor an audit entry.
    assert outbox.envelopes == [] and audit.entries == []

    await approve(service, request.id)
    overtime_repo.lose_next_transition = True
    with pytest.raises(OvertimeNotManagerApprovedError):
        await service.verify(decider(), request.id, OvertimeDecision(), NOW)

    overtime_repo.lose_next_transition = True
    with pytest.raises(OvertimeNotOpenError):
        await service.reject(decider(), request.id, OvertimeDecision(), NOW)

    second = await submit(service, work_date=date(2026, 10, 13))
    overtime_repo.lose_next_transition = True
    with pytest.raises(OvertimeNotOpenError):
        await service.withdraw(identity(), second.id, NOW)


async def test_list_requests_scopes_by_role_and_filters(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)
    seed_employee(overtime_repo, employee_id=MANAGER)
    seed_employee(overtime_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(overtime_repo, employee_id=OTHER)

    own = await submit(service, work_date=date(2026, 10, 1))
    manager_request = await submit(
        service, caller=identity(employee_id=MANAGER), work_date=date(2026, 10, 2)
    )
    report_request = await submit(
        service, caller=identity(employee_id=REPORT), work_date=date(2026, 10, 3)
    )
    other_request = await submit(
        service, caller=identity(employee_id=OTHER), work_date=date(2026, 10, 4)
    )

    own_page = await service.list_requests(identity(), page=PageParams())
    assert [item.id for item in own_page.items] == [own.id]
    assert own_page.page.total_items == 1

    manager_page = await service.list_requests(manager(), page=PageParams())
    assert {item.id for item in manager_page.items} == {
        manager_request.id,
        report_request.id,
    }

    hr_page = await service.list_requests(decider(), page=PageParams())
    assert hr_page.page.total_items == 4
    assert other_request.id in {item.id for item in hr_page.items}

    filtered = await service.list_requests(
        decider(), employee_id=REPORT, page=PageParams()
    )
    assert [item.id for item in filtered.items] == [report_request.id]

    await approve(service, own.id)
    by_status = await service.list_requests(
        decider(), status=MANAGER_APPROVED, page=PageParams()
    )
    assert [item.id for item in by_status.items] == [own.id]

    # from/to filter work_date inclusively.
    in_range = await service.list_requests(
        decider(),
        from_date=date(2026, 10, 2),
        to_date=date(2026, 10, 3),
        page=PageParams(),
    )
    assert {item.id for item in in_range.items} == {
        manager_request.id,
        report_request.id,
    }

    first_page = await service.list_requests(
        decider(), page=PageParams(page=1, page_size=2)
    )
    second_page = await service.list_requests(
        decider(), page=PageParams(page=2, page_size=2)
    )
    assert first_page.page.total_items == 4
    assert len(first_page.items) == 2 and len(second_page.items) == 2
    assert {item.id for item in first_page.items}.isdisjoint(
        {item.id for item in second_page.items}
    )

    with pytest.raises(EmployeeNotFoundError):
        await service.list_requests(identity(), employee_id=REPORT, page=PageParams())
    with pytest.raises(EmployeeNotFoundError):
        await service.list_requests(manager(), employee_id=OTHER, page=PageParams())
    with pytest.raises(EmployeeNotFoundError):
        # Tenant-wide viewers still get a 404 for an unknown employee filter.
        await service.list_requests(decider(), employee_id=uuid7(), page=PageParams())

    cross_tenant = await service.list_requests(
        identity(tenant_id=TENANT_B), page=PageParams()
    )
    assert cross_tenant.items == [] and cross_tenant.page.total_items == 0


async def test_get_request_never_leaks_out_of_scope_ids(
    service: OvertimeService, overtime_repo: FakeOvertimeRepo
) -> None:
    seed_employee(overtime_repo)
    seed_employee(overtime_repo, employee_id=MANAGER)
    seed_employee(overtime_repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(overtime_repo, employee_id=OTHER)

    own = await submit(service, work_date=date(2026, 10, 1))
    report_request = await submit(
        service, caller=identity(employee_id=REPORT), work_date=date(2026, 10, 2)
    )
    other_request = await submit(
        service, caller=identity(employee_id=OTHER), work_date=date(2026, 10, 3)
    )

    assert (await service.get_request(identity(), own.id)).id == own.id
    with pytest.raises(OvertimeRequestNotFoundError):
        await service.get_request(identity(), report_request.id)

    assert (
        await service.get_request(manager(), report_request.id)
    ).id == report_request.id
    with pytest.raises(OvertimeRequestNotFoundError):
        await service.get_request(manager(), other_request.id)

    assert (
        await service.get_request(decider(), other_request.id)
    ).id == other_request.id
    with pytest.raises(OvertimeRequestNotFoundError):
        await service.get_request(identity(tenant_id=TENANT_B), own.id)


@pytest.mark.parametrize(
    "hours",
    [Decimal("0"), Decimal("-1"), Decimal("12.01"), Decimal("1.234")],
)
def test_hours_out_of_bounds_or_precision_is_rejected(hours: Decimal) -> None:
    with pytest.raises(ValidationError):
        OvertimeRequestCreate(work_date=TODAY, hours=hours)


@pytest.mark.parametrize(
    "hours",
    [Decimal("0.01"), Decimal("12"), Decimal("12.00"), Decimal("1.23")],
)
def test_hours_boundaries_are_accepted(hours: Decimal) -> None:
    payload = OvertimeRequestCreate(work_date=TODAY, hours=hours)
    assert payload.hours == hours


def test_reason_and_note_length_bounds() -> None:
    with pytest.raises(ValidationError):
        OvertimeRequestCreate(work_date=TODAY, hours=Decimal("1"), reason="")
    with pytest.raises(ValidationError):
        OvertimeRequestCreate(work_date=TODAY, hours=Decimal("1"), reason="x" * 501)
    assert (
        OvertimeRequestCreate(
            work_date=TODAY, hours=Decimal("1"), reason="x" * 500
        ).reason
        == "x" * 500
    )

    with pytest.raises(ValidationError):
        OvertimeDecision(note="")
    with pytest.raises(ValidationError):
        OvertimeDecision(note="x" * 501)
    assert OvertimeDecision(note="x" * 500).note == "x" * 500


async def test_pii_never_reaches_events_or_audit_values(
    service: OvertimeService,
    overtime_repo: FakeOvertimeRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(overtime_repo)
    seed_employee(overtime_repo, employee_id=MANAGER)

    request = await submit(
        service,
        work_date=date(2026, 10, 12),
        reason="Medical appointment",
    )
    await service.approve(
        decider(), request.id, OvertimeDecision(note="Manager private note"), NOW
    )
    await service.verify(
        decider(), request.id, OvertimeDecision(note="HR private note"), NOW
    )

    events = json.dumps(outbox.envelopes)
    audit_values = json.dumps([entry.changes for entry in audit.entries])
    for secret in (
        "Medical appointment",
        "Manager private note",
        "HR private note",
    ):
        assert secret not in events
        assert secret not in audit_values

    assert [entry.action for entry in audit.entries] == [
        "overtime.requested",
        "overtime.approved",
        "overtime.verified",
    ]
    assert "changed" in audit_values
