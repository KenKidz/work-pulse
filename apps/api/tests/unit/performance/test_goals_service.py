"""Unit tests for `GoalService` — the pinned goal rules (T5).

Every call passes an explicit `NOW`; the service never reads the wall clock
(`.ai/testing.md`: no wall-time assertions). The fake repository enforces the
same tenant scope, list filters and conditional-transition guards as the
Postgres adapter, so these tests exercise business logic, not I/O.

Covered here (`.ai/features/performance.md` → T5, acceptance criteria 1–2):
the lifecycle, every pinned failure code, role/scope checks, tenant isolation,
the lost-race backstops, and the audit/event split (progress emits no event but
is audited, PII never carries values).
"""

import json
from datetime import date
from uuid import UUID

import pytest

from app.domains.performance.exceptions import (
    EmployeeNotActiveError,
    EmployeeNotFoundError,
    GoalNotActiveError,
    GoalNotDraftError,
    GoalNotFoundError,
    GoalTerminalError,
    PerformanceDeniedError,
)
from app.domains.performance.schemas import (
    Goal,
    GoalCreate,
    GoalStatus,
    GoalUpdate,
)
from app.domains.performance.service import GoalService
from app.shared.identity import Identity, Role
from app.shared.pagination import PageParams
from tests.unit.organizations.fakes import RecordingAuditWriter
from tests.unit.outbox.fakes import RecordingEventOutbox
from tests.unit.performance.conftest import (
    EMPLOYEE,
    MANAGER,
    NOW,
    OTHER,
    PEER,
    REPORT,
    TENANT_A,
    TENANT_B,
    USER,
    identity,
    seed_employee,
)
from tests.unit.performance.fakes import FakeEmployeeScope, FakePerformanceRepo

PII_DESCRIPTION = "Secret goal description — pii-goal-description"
PII_TITLE = "Ship the secret roadmap — pii-goal-title"


@pytest.fixture
def service(
    repo: FakePerformanceRepo,
    scope: FakeEmployeeScope,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> GoalService:
    return GoalService(repo=repo, scope=scope, audit=audit, outbox=outbox)


async def _create(
    service: GoalService,
    *,
    caller: Identity | None = None,
    employee_id: UUID | None = None,
    title: str = PII_TITLE,
    description: str | None = None,
    due_date: date | None = None,
    progress_pct: int = 0,
) -> Goal:
    payload = GoalCreate(
        employee_id=employee_id,
        title=title,
        description=description,
        due_date=due_date,
        progress_pct=progress_pct,
    )
    return await service.create_goal(
        caller if caller is not None else identity(role=Role.HR),
        payload,
        NOW,
    )


# --- create ------------------------------------------------------------------


async def test_create_defaults_to_own_employee_with_event_and_audit(
    service: GoalService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)

    goal = await service.create_goal(
        caller,
        GoalCreate(
            title=PII_TITLE,
            description=PII_DESCRIPTION,
            due_date=date(2026, 11, 1),
            progress_pct=25,
        ),
        NOW,
    )

    assert goal.employee_id == EMPLOYEE
    assert goal.status is GoalStatus.DRAFT
    assert goal.progress_pct == 25
    assert goal.created_at is not None

    [envelope] = outbox.envelopes
    assert envelope["event_type"] == "performance.goal_created"
    assert envelope["version"] == 1
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["actor_id"] == str(USER)
    assert envelope["data"] == {
        "goal_id": str(goal.id),
        "employee_id": str(EMPLOYEE),
        "status": "draft",
    }
    assert PII_TITLE not in json.dumps(envelope)
    assert PII_DESCRIPTION not in json.dumps(envelope)

    [entry] = audit.entries
    assert entry.action == "goal.created"
    assert entry.entity_type == "goal"
    assert entry.entity_id == goal.id
    assert entry.changes is not None
    assert entry.changes["employee_id"] == {"old": None, "new": str(EMPLOYEE)}
    assert entry.changes["status"] == {"old": None, "new": "draft"}
    assert entry.changes["progress_pct"] == {"old": None, "new": 25}
    assert entry.changes["due_date"] == {"old": None, "new": "2026-11-01"}
    assert entry.changes["title"] == "changed"  # PII: marker only
    assert entry.changes["description"] == "changed"  # PII: marker only
    encoded = json.dumps(entry.changes)
    assert PII_TITLE not in encoded
    assert PII_DESCRIPTION not in encoded


async def test_create_for_a_report_is_allowed_but_out_of_scope_is_404(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    seed_employee(repo, employee_id=EMPLOYEE)
    seed_employee(repo, employee_id=MANAGER)
    seed_employee(repo, employee_id=REPORT, manager_id=MANAGER)
    seed_employee(repo, employee_id=PEER)
    manager = identity(role=Role.MANAGER, employee_id=MANAGER)

    report_goal = await service.create_goal(
        manager, GoalCreate(employee_id=REPORT, title="Report goal"), NOW
    )
    assert report_goal.employee_id == REPORT

    own_goal = await service.create_goal(manager, GoalCreate(title="Manager goal"), NOW)
    assert own_goal.employee_id == MANAGER

    with pytest.raises(EmployeeNotFoundError):
        await service.create_goal(
            manager, GoalCreate(employee_id=PEER, title="Peer goal"), NOW
        )

    with pytest.raises(EmployeeNotFoundError):
        await service.create_goal(
            identity(role=Role.EMPLOYEE),
            GoalCreate(employee_id=REPORT, title="Colleague goal"),
            NOW,
        )


async def test_create_requires_an_active_subject(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    seed_employee(repo, active=False)

    with pytest.raises(EmployeeNotActiveError):
        await service.create_goal(
            identity(role=Role.EMPLOYEE), GoalCreate(title="Terminated self"), NOW
        )

    with pytest.raises(EmployeeNotActiveError):
        await service.create_goal(
            identity(role=Role.HR, employee_id=OTHER),
            GoalCreate(employee_id=EMPLOYEE, title="Terminated subject"),
            NOW,
        )


async def test_create_rejects_unknown_subjects_and_missing_links(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    seed_employee(repo)

    with pytest.raises(EmployeeNotFoundError):
        await service.create_goal(
            identity(role=Role.HR),
            GoalCreate(employee_id=OTHER, title="Unknown subject"),
            NOW,
        )

    with pytest.raises(PerformanceDeniedError):
        await service.create_goal(
            identity(role=Role.HR, employee_id=None),
            GoalCreate(title="No link"),
            NOW,
        )
    with pytest.raises(PerformanceDeniedError):
        await service.create_goal(
            identity(role=Role.EMPLOYEE, employee_id=None),
            GoalCreate(title="No link"),
            NOW,
        )


async def test_finance_cannot_manage_goals(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    seed_employee(repo)

    with pytest.raises(PerformanceDeniedError):
        await service.create_goal(
            identity(role=Role.FINANCE), GoalCreate(title="Finance goal"), NOW
        )


# --- lifecycle ---------------------------------------------------------------


async def test_activate_moves_draft_to_active_with_event_and_audit(
    service: GoalService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)

    activated = await service.activate_goal(caller, goal.id, NOW)

    assert activated.status is GoalStatus.ACTIVE
    assert outbox.envelopes[-1]["event_type"] == "performance.goal_activated"
    assert outbox.envelopes[-1]["data"] == {
        "goal_id": str(goal.id),
        "employee_id": str(EMPLOYEE),
        "status": "active",
    }
    assert audit.entries[-1].action == "goal.activated"
    assert audit.entries[-1].changes == {"status": {"old": "draft", "new": "active"}}

    with pytest.raises(GoalNotDraftError):
        await service.activate_goal(caller, goal.id, NOW)


async def test_complete_requires_active_and_stamps_completed_at(
    service: GoalService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)

    with pytest.raises(GoalNotActiveError):
        await service.complete_goal(caller, goal.id, NOW)

    await service.activate_goal(caller, goal.id, NOW)
    completed = await service.complete_goal(caller, goal.id, NOW)

    assert completed.status is GoalStatus.COMPLETED
    assert completed.completed_at == NOW
    assert outbox.envelopes[-1]["event_type"] == "performance.goal_completed"
    assert outbox.envelopes[-1]["data"]["status"] == "completed"
    assert audit.entries[-1].action == "goal.completed"
    assert audit.entries[-1].changes == {
        "status": {"old": "active", "new": "completed"}
    }

    with pytest.raises(GoalNotActiveError):
        await service.complete_goal(caller, goal.id, NOW)


@pytest.mark.parametrize("activate_first", [False, True])
async def test_cancel_is_terminal_from_draft_and_active(
    service: GoalService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
    activate_first: bool,
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)
    expected_old = "draft"
    if activate_first:
        await service.activate_goal(caller, goal.id, NOW)
        expected_old = "active"

    cancelled = await service.cancel_goal(caller, goal.id, NOW)

    assert cancelled.status is GoalStatus.CANCELLED
    assert outbox.envelopes[-1]["event_type"] == "performance.goal_cancelled"
    assert outbox.envelopes[-1]["data"]["status"] == "cancelled"
    assert audit.entries[-1].action == "goal.cancelled"
    assert audit.entries[-1].changes == {
        "status": {"old": expected_old, "new": "cancelled"}
    }

    with pytest.raises(GoalTerminalError):
        await service.cancel_goal(caller, goal.id, NOW)


async def test_terminal_goal_is_immutable(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)
    await service.activate_goal(caller, goal.id, NOW)
    await service.complete_goal(caller, goal.id, NOW)

    with pytest.raises(GoalTerminalError):
        await service.update_goal(caller, goal.id, GoalUpdate(progress_pct=100), NOW)
    with pytest.raises(GoalTerminalError):
        await service.update_goal(caller, goal.id, GoalUpdate(), NOW)
    with pytest.raises(GoalTerminalError):
        await service.cancel_goal(caller, goal.id, NOW)


async def test_progress_update_is_audit_only(
    service: GoalService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)
    await service.activate_goal(caller, goal.id, NOW)
    envelopes_after_transitions = len(outbox.envelopes)
    entries_after_transitions = len(audit.entries)

    updated = await service.update_goal(
        caller,
        goal.id,
        GoalUpdate(
            title="Renamed goal",
            description=PII_DESCRIPTION,
            progress_pct=60,
            due_date=date(2026, 12, 1),
        ),
        NOW,
    )

    assert updated.progress_pct == 60
    assert updated.title == "Renamed goal"
    # Progress is a field update, not a transition: no event.
    assert len(outbox.envelopes) == envelopes_after_transitions
    assert len(audit.entries) == entries_after_transitions + 1
    [entry] = audit.entries[entries_after_transitions:]
    assert entry.action == "goal.updated"
    assert entry.changes is not None
    assert entry.changes["progress_pct"] == {"old": 0, "new": 60}
    assert entry.changes["due_date"] == {"old": None, "new": "2026-12-01"}
    assert entry.changes["title"] == "changed"
    assert entry.changes["description"] == "changed"
    encoded = json.dumps(entry.changes)
    assert "Renamed goal" not in encoded
    assert PII_DESCRIPTION not in encoded


async def test_empty_update_is_a_noop(
    service: GoalService,
    repo: FakePerformanceRepo,
    audit: RecordingAuditWriter,
    outbox: RecordingEventOutbox,
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)

    unchanged = await service.update_goal(caller, goal.id, GoalUpdate(), NOW)

    assert unchanged == goal
    assert len(audit.entries) == 1
    assert len(outbox.envelopes) == 1


async def test_lost_transition_races_map_to_the_pinned_errors(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    seed_employee(repo)
    caller = identity(role=Role.EMPLOYEE)
    goal = await _create(service, caller=caller)

    repo.lose_next_transition = True
    with pytest.raises(GoalNotDraftError):
        await service.activate_goal(caller, goal.id, NOW)

    await service.activate_goal(caller, goal.id, NOW)

    repo.lose_next_transition = True
    with pytest.raises(GoalNotActiveError):
        await service.complete_goal(caller, goal.id, NOW)

    repo.lose_next_transition = True
    with pytest.raises(GoalTerminalError):
        await service.cancel_goal(caller, goal.id, NOW)

    repo.lose_next_transition = True
    with pytest.raises(GoalTerminalError):
        await service.update_goal(caller, goal.id, GoalUpdate(progress_pct=10), NOW)


# --- visibility and tenant isolation ----------------------------------------


async def _seed_visible_world(
    service: GoalService, repo: FakePerformanceRepo
) -> dict[str, Goal]:
    """Four active employees, one goal each (created by HR)."""
    for employee_id, manager_id in (
        (EMPLOYEE, None),
        (MANAGER, None),
        (REPORT, MANAGER),
        (PEER, None),
    ):
        seed_employee(repo, employee_id=employee_id, manager_id=manager_id)
    hr = identity(role=Role.HR, employee_id=None)
    goals: dict[str, Goal] = {}
    for employee_id in (EMPLOYEE, MANAGER, REPORT, PEER):
        goals[str(employee_id)] = await service.create_goal(
            hr, GoalCreate(employee_id=employee_id, title="Goal"), NOW
        )
    return goals


async def test_list_scopes_to_own_team_or_tenant(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    goals = await _seed_visible_world(service, repo)
    page = PageParams()

    employee_list = await service.list_goals(identity(role=Role.EMPLOYEE), page=page)
    assert [item.id for item in employee_list.items] == [goals[str(EMPLOYEE)].id]

    finance_list = await service.list_goals(identity(role=Role.FINANCE), page=page)
    assert [item.id for item in finance_list.items] == [goals[str(EMPLOYEE)].id]

    manager_list = await service.list_goals(
        identity(role=Role.MANAGER, employee_id=MANAGER), page=page
    )
    assert {item.id for item in manager_list.items} == {
        goals[str(MANAGER)].id,
        goals[str(REPORT)].id,
    }

    hr_list = await service.list_goals(
        identity(role=Role.HR, employee_id=None), page=page
    )
    assert len(hr_list.items) == 4

    filtered = await service.list_goals(
        identity(role=Role.MANAGER, employee_id=MANAGER),
        employee_id=REPORT,
        page=page,
    )
    assert [item.id for item in filtered.items] == [goals[str(REPORT)].id]


async def test_list_status_filter_and_foreign_employee_filters(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    goals = await _seed_visible_world(service, repo)
    caller = identity(role=Role.EMPLOYEE)
    await service.activate_goal(caller, goals[str(EMPLOYEE)].id, NOW)
    page = PageParams()

    active = await service.list_goals(caller, status=GoalStatus.ACTIVE, page=page)
    assert [item.id for item in active.items] == [goals[str(EMPLOYEE)].id]
    draft = await service.list_goals(caller, status=GoalStatus.DRAFT, page=page)
    assert draft.items == []

    with pytest.raises(EmployeeNotFoundError):
        await service.list_goals(caller, employee_id=REPORT, page=page)
    with pytest.raises(EmployeeNotFoundError):
        await service.list_goals(
            identity(role=Role.MANAGER, employee_id=MANAGER),
            employee_id=PEER,
            page=page,
        )
    with pytest.raises(EmployeeNotFoundError):
        await service.list_goals(
            identity(role=Role.HR),
            employee_id=OTHER,
            page=page,
        )


async def test_detail_is_scope_checked_and_tenant_isolated(
    service: GoalService, repo: FakePerformanceRepo
) -> None:
    goals = await _seed_visible_world(service, repo)
    own_goal = goals[str(EMPLOYEE)]
    peer_goal = goals[str(PEER)]
    report_goal = goals[str(REPORT)]

    assert (
        await service.get_goal(identity(role=Role.EMPLOYEE), own_goal.id)
    ).id == own_goal.id
    with pytest.raises(GoalNotFoundError):
        await service.get_goal(identity(role=Role.EMPLOYEE), peer_goal.id)

    manager = identity(role=Role.MANAGER, employee_id=MANAGER)
    assert (await service.get_goal(manager, report_goal.id)).id == report_goal.id
    with pytest.raises(GoalNotFoundError):
        await service.get_goal(manager, peer_goal.id)

    # Tenant B sees neither the row nor the employee behind it.
    tenant_b = identity(tenant_id=TENANT_B, role=Role.HR, employee_id=None)
    with pytest.raises(GoalNotFoundError):
        await service.get_goal(tenant_b, own_goal.id)
    with pytest.raises(EmployeeNotFoundError):
        await service.list_goals(tenant_b, employee_id=EMPLOYEE, page=PageParams())
    with pytest.raises(EmployeeNotFoundError):
        await service.create_goal(
            tenant_b, GoalCreate(employee_id=EMPLOYEE, title="Cross tenant"), NOW
        )
