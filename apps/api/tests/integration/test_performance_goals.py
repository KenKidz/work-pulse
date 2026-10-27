"""HTTP integration tests for performance goals, audit and outbox (T5).

Every test drives the real router, `GoalService` and PostgreSQL adapter through
the shared session. The router clock is injected (`performance_api._now`), so
timestamps never depend on wall time.

Covered here (`.ai/features/performance.md` → T5, acceptance criteria 1–2):

- create → `201 draft` + one audit entry + a valid `performance.goal_created`
  envelope; activate/complete each add their own audit row and envelope in the
  mutation's transaction;
- the pinned failure codes at the HTTP edge (`goal_not_active`,
  `goal_not_draft`, `goal_terminal`) and no audit/event rows for failures;
- progress is a field update: no event, one `goal.updated` audit entry;
- role and subject scoping (own / team / tenant), foreign ids as `404`,
  Finance denied;
- terminated subjects (`409 employee_not_active`) and fail-closed missing links;
- list filters, clamped pagination, and a concurrent-activate race with exactly
  one winner.
"""

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import performance as performance_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.performance.exceptions import (
    GoalNotDraftError,
    GoalTerminalError,
)
from app.domains.performance.schemas import GoalStatus
from app.domains.performance.service import GoalService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.manager_scope import PostgresEmployeeScope
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.performance import (
    PostgresPerformanceRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MANAGER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000021")
REPORT_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000022")
PEER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000023")
TERMINATED_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000024")
UNKNOWN_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000ee")

PINNED_GOAL_KEYS = {
    "id",
    "employee_id",
    "title",
    "description",
    "due_date",
    "progress_pct",
    "status",
    "created_at",
    "updated_at",
    "completed_at",
}

PII_TITLE = "Secret roadmap — pii-goal-title"
PII_DESCRIPTION = "Private plan — pii-goal-description"


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


async def _seed_employee(
    session: AsyncSession,
    refs: tuple[str, str, str],
    *,
    employee_id: UUID = ACTOR,
    code: str = "EMP-00009",
    email: str = "self@example.test",
    first_name: str = "Actor",
    last_name: str = "Self",
    manager_id: UUID | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
    tenant_id: UUID = TENANT_A,
) -> None:
    await PostgresEmployeeRepository(session).add(
        tenant_id,
        employee_id,
        employee_code=code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=manager_id,
        employment_type=EmploymentType.FULL_TIME,
        status=status,
        hired_at=date(2026, 1, 5),
    )


async def _seed_world(
    client_factory: ClientFactory, session: AsyncSession
) -> tuple[str, str, str]:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)
    await _seed_employee(
        session,
        refs,
        employee_id=MANAGER_EMPLOYEE,
        code="EMP-00021",
        email="manager@example.test",
        first_name="Morgan",
        last_name="Manager",
    )
    await _seed_employee(
        session,
        refs,
        employee_id=REPORT_EMPLOYEE,
        code="EMP-00022",
        email="report@example.test",
        first_name="Riley",
        last_name="Report",
        manager_id=MANAGER_EMPLOYEE,
    )
    await _seed_employee(
        session,
        refs,
        employee_id=PEER_EMPLOYEE,
        code="EMP-00023",
        email="peer@example.test",
        first_name="Parker",
        last_name="Peer",
    )
    return refs


async def _create_goal(
    client: AsyncClient,
    *,
    employee_id: UUID | None = None,
    title: str = "Goal",
    description: str | None = None,
    due_date: str | None = None,
    progress_pct: int = 0,
) -> Response:
    body: dict[str, Any] = {"title": title, "progress_pct": progress_pct}
    if employee_id is not None:
        body["employee_id"] = str(employee_id)
    if description is not None:
        body["description"] = description
    if due_date is not None:
        body["due_date"] = due_date
    return await client.post("/api/v1/performance/goals", json=body)


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "goal")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


async def test_create_activate_complete_write_events_and_audit(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        created = await _create_goal(
            employee,
            title=PII_TITLE,
            description=PII_DESCRIPTION,
            due_date="2026-11-01",
            progress_pct=10,
        )
        goal_id = created.json()["id"]
        activated = await employee.post(f"/api/v1/performance/goals/{goal_id}/activate")
        completed = await employee.post(f"/api/v1/performance/goals/{goal_id}/complete")

    assert created.status_code == 201
    created_body = created.json()
    assert set(created_body) == PINNED_GOAL_KEYS
    assert created_body["employee_id"] == str(ACTOR)
    assert created_body["title"] == PII_TITLE
    assert created_body["description"] == PII_DESCRIPTION
    assert created_body["due_date"] == "2026-11-01"
    assert created_body["progress_pct"] == 10
    assert created_body["status"] == "draft"
    assert created_body["completed_at"] is None

    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert completed.status_code == 200
    completed_body = completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["completed_at"] is not None
    # Progress is never auto-forwarded by completion.
    assert completed_body["progress_pct"] == 10

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "goal.created",
        "goal.activated",
        "goal.completed",
    ]
    assert {row.entity_id for row in audit} == {UUID(goal_id)}
    assert {row.actor_id for row in audit} == {USER}
    assert audit[0].changes is not None
    assert audit[0].changes["employee_id"] == {"old": None, "new": str(ACTOR)}
    assert audit[0].changes["status"] == {"old": None, "new": "draft"}
    assert audit[0].changes["progress_pct"] == {"old": None, "new": 10}
    assert audit[0].changes["due_date"] == {"old": None, "new": "2026-11-01"}
    assert audit[0].changes["title"] == "changed"  # PII: marker only
    assert audit[0].changes["description"] == "changed"
    assert audit[1].changes == {"status": {"old": "draft", "new": "active"}}
    assert audit[2].changes == {"status": {"old": "active", "new": "completed"}}
    encoded_audit = str([row.changes for row in audit])
    assert PII_TITLE not in encoded_audit
    assert PII_DESCRIPTION not in encoded_audit

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "performance.goal_created",
        "performance.goal_activated",
        "performance.goal_completed",
    ]
    assert [row.payload["data"] for row in outbox] == [
        {"goal_id": goal_id, "employee_id": str(ACTOR), "status": "draft"},
        {"goal_id": goal_id, "employee_id": str(ACTOR), "status": "active"},
        {"goal_id": goal_id, "employee_id": str(ACTOR), "status": "completed"},
    ]
    assert {row.payload["version"] for row in outbox} == {1}
    assert {row.payload["tenant_id"] for row in outbox} == {str(TENANT_A)}
    assert {row.payload["actor_id"] for row in outbox} == {str(USER)}
    assert {row.payload["occurred_at"] for row in outbox} == {CLOCK.isoformat()}
    encoded_outbox = str([row.payload for row in outbox])
    assert PII_TITLE not in encoded_outbox
    assert PII_DESCRIPTION not in encoded_outbox


async def test_wrong_state_and_terminal_failures_write_nothing(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        draft = (await _create_goal(employee)).json()
        complete_draft = await employee.post(
            f"/api/v1/performance/goals/{draft['id']}/complete"
        )
        activated = await employee.post(
            f"/api/v1/performance/goals/{draft['id']}/activate"
        )
        assert activated.status_code == 200
        activate_again = await employee.post(
            f"/api/v1/performance/goals/{draft['id']}/activate"
        )
        completed = await employee.post(
            f"/api/v1/performance/goals/{draft['id']}/complete"
        )
        assert completed.status_code == 200

        audit_before = len(await _audit_rows(session))
        outbox_before = len(await _outbox_rows(session))

        patch_terminal = await employee.patch(
            f"/api/v1/performance/goals/{draft['id']}",
            json={"progress_pct": 100},
        )
        cancel_terminal = await employee.post(
            f"/api/v1/performance/goals/{draft['id']}/cancel"
        )

    _assert_error(complete_draft, 409, "goal_not_active")
    _assert_error(activate_again, 409, "goal_not_draft")
    _assert_error(patch_terminal, 409, "goal_terminal")
    _assert_error(cancel_terminal, 409, "goal_terminal")

    # The two failed requests added no rows.
    assert len(await _audit_rows(session)) == audit_before
    assert len(await _outbox_rows(session)) == outbox_before

    # `activate` from a cancelled row is still the draft-only refusal.
    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        cancelled = (await _create_goal(employee)).json()
        assert (
            await employee.post(f"/api/v1/performance/goals/{cancelled['id']}/cancel")
        ).status_code == 200
        activate_cancelled = await employee.post(
            f"/api/v1/performance/goals/{cancelled['id']}/activate"
        )

    _assert_error(activate_cancelled, 409, "goal_not_draft")


async def test_progress_update_is_audit_only(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        goal = (await _create_goal(employee, progress_pct=5)).json()
        assert (
            await employee.post(f"/api/v1/performance/goals/{goal['id']}/activate")
        ).status_code == 200
        outbox_before = len(await _outbox_rows(session))
        audit_before = len(await _audit_rows(session))

        updated = await employee.patch(
            f"/api/v1/performance/goals/{goal['id']}",
            json={
                "progress_pct": 65,
                "title": "Renamed — pii-goal-renamed",
                "description": PII_DESCRIPTION,
                "due_date": "2026-12-01",
            },
        )

    assert updated.status_code == 200
    body = updated.json()
    assert body["progress_pct"] == 65
    assert body["title"] == "Renamed — pii-goal-renamed"
    assert body["status"] == "active"

    # A field update is not a transition: no event envelope.
    assert len(await _outbox_rows(session)) == outbox_before

    audit = await _audit_rows(session)
    assert len(audit) == audit_before + 1
    [entry] = audit[audit_before:]
    assert entry.action == "goal.updated"
    assert entry.changes is not None
    assert entry.changes["progress_pct"] == {"old": 5, "new": 65}
    assert entry.changes["due_date"] == {"old": None, "new": "2026-12-01"}
    assert entry.changes["title"] == "changed"
    assert entry.changes["description"] == "changed"
    encoded = str(entry.changes)
    assert "pii-goal-renamed" not in encoded
    assert PII_DESCRIPTION not in encoded


async def test_goal_visibility_scope_and_tenant_isolation(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    hr = identity(role=Role.HR, employee_id=None)
    manager = identity(role=Role.MANAGER, employee_id=MANAGER_EMPLOYEE)

    async with client_factory(hr) as hr_client:
        goals: dict[UUID, str] = {}
        for employee_id in (
            ACTOR,
            MANAGER_EMPLOYEE,
            REPORT_EMPLOYEE,
            PEER_EMPLOYEE,
        ):
            response = await _create_goal(hr_client, employee_id=employee_id)
            assert response.status_code == 201
            goals[employee_id] = response.json()["id"]

        hr_list = await hr_client.get("/api/v1/performance/goals")
        hr_filtered = await hr_client.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(REPORT_EMPLOYEE)},
        )
        hr_unknown = await hr_client.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(UNKNOWN_EMPLOYEE)},
        )

    assert hr_list.status_code == 200
    assert len(hr_list.json()["items"]) == 4
    assert [item["id"] for item in hr_filtered.json()["items"]] == [
        goals[REPORT_EMPLOYEE]
    ]
    _assert_error(hr_unknown, 404, "employee_not_found")

    async with client_factory(manager) as manager_client:
        manager_list = await manager_client.get("/api/v1/performance/goals")
        report_detail = await manager_client.get(
            f"/api/v1/performance/goals/{goals[REPORT_EMPLOYEE]}"
        )
        peer_detail = await manager_client.get(
            f"/api/v1/performance/goals/{goals[PEER_EMPLOYEE]}"
        )
        peer_filter = await manager_client.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(PEER_EMPLOYEE)},
        )

    assert manager_list.status_code == 200
    assert {item["id"] for item in manager_list.json()["items"]} == {
        goals[MANAGER_EMPLOYEE],
        goals[REPORT_EMPLOYEE],
    }
    assert report_detail.status_code == 200
    _assert_error(peer_detail, 404, "goal_not_found")
    _assert_error(peer_filter, 404, "employee_not_found")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        own_list = await employee.get("/api/v1/performance/goals")
        peer_detail = await employee.get(
            f"/api/v1/performance/goals/{goals[PEER_EMPLOYEE]}"
        )
        foreign_filter = await employee.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(REPORT_EMPLOYEE)},
        )
        create_for_colleague = await _create_goal(employee, employee_id=REPORT_EMPLOYEE)

    assert own_list.status_code == 200
    assert [item["id"] for item in own_list.json()["items"]] == [goals[ACTOR]]
    _assert_error(peer_detail, 404, "goal_not_found")
    _assert_error(foreign_filter, 404, "employee_not_found")
    _assert_error(create_for_colleague, 404, "employee_not_found")

    async with client_factory(identity(role=Role.FINANCE)) as finance:
        finance_list = await finance.get("/api/v1/performance/goals")
        finance_create = await _create_goal(finance)

    assert [item["id"] for item in finance_list.json()["items"]] == [goals[ACTOR]]
    _assert_error(finance_create, 403, "performance_denied")

    tenant_b = identity(tenant=TENANT_B, role=Role.HR, employee_id=None)
    async with client_factory(tenant_b) as tenant_b_client:
        cross_detail = await tenant_b_client.get(
            f"/api/v1/performance/goals/{goals[ACTOR]}"
        )
        cross_filter = await tenant_b_client.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(ACTOR)},
        )
        cross_create = await _create_goal(tenant_b_client, employee_id=ACTOR)

    _assert_error(cross_detail, 404, "goal_not_found")
    _assert_error(cross_filter, 404, "employee_not_found")
    _assert_error(cross_create, 404, "employee_not_found")


async def test_terminated_subject_and_missing_link_are_pinned_errors(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    refs = await _seed_world(client_factory, session)
    await _seed_employee(
        session,
        refs,
        employee_id=TERMINATED_EMPLOYEE,
        code="EMP-00024",
        email="former@example.test",
        first_name="Forrest",
        last_name="Former",
        status=EmployeeStatus.TERMINATED,
    )

    async with client_factory(
        identity(role=Role.EMPLOYEE, employee_id=TERMINATED_EMPLOYEE)
    ) as terminated:
        self_goal = await _create_goal(terminated)
    _assert_error(self_goal, 409, "employee_not_active")

    # The linked actor is active; a terminated subject is refused for HR too.
    async with client_factory(identity(role=Role.HR, employee_id=None)) as hr:
        terminated_goal = await _create_goal(hr, employee_id=TERMINATED_EMPLOYEE)
        missing_link = await _create_goal(hr)

    assert terminated_goal.status_code == 409
    assert terminated_goal.json()["error"]["code"] == "employee_not_active"
    _assert_error(missing_link, 403, "performance_denied")

    # History stays readable for a terminated subject.
    async with client_factory(identity(role=Role.HR, employee_id=None)) as hr:
        history = await hr.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(TERMINATED_EMPLOYEE)},
        )
    assert history.status_code == 200
    assert history.json()["items"] == []


async def test_list_filters_and_clamped_pagination(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        first = (await _create_goal(employee, title="First")).json()
        second = (await _create_goal(employee, title="Second")).json()
        third = (await _create_goal(employee, title="Third")).json()
        assert (
            await employee.post(f"/api/v1/performance/goals/{first['id']}/activate")
        ).status_code == 200

        listed = await employee.get("/api/v1/performance/goals")
        active = await employee.get(
            "/api/v1/performance/goals", params={"status": "active"}
        )
        clamped = await employee.get(
            "/api/v1/performance/goals", params={"page": 0, "page_size": 1000}
        )
        detail = await employee.get(f"/api/v1/performance/goals/{second['id']}")

    assert listed.status_code == 200
    body = listed.json()
    assert set(body["page"]) == {
        "page",
        "page_size",
        "total_items",
        "total_pages",
    }
    assert body["page"]["total_items"] == 3
    assert {item["id"] for item in body["items"]} == {
        first["id"],
        second["id"],
        third["id"],
    }
    assert active.status_code == 200
    assert [item["id"] for item in active.json()["items"]] == [first["id"]]
    assert clamped.status_code == 200
    assert clamped.json()["page"] == {
        "page": 1,
        "page_size": 100,
        "total_items": 3,
        "total_pages": 1,
    }
    assert detail.status_code == 200
    assert detail.json()["id"] == second["id"]

    async with client_factory(identity(role=Role.HR, employee_id=None)) as hr:
        hr_filtered = await hr.get(
            "/api/v1/performance/goals",
            params={"employee_id": str(ACTOR), "status": "draft"},
        )
    assert hr_filtered.status_code == 200
    assert {item["id"] for item in hr_filtered.json()["items"]} == {
        second["id"],
        third["id"],
    }


async def test_concurrent_activate_has_exactly_one_winner(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        goal_id = UUID((await _create_goal(employee)).json()["id"])

    # The fresh sessions in the race must see the committed setup rows.
    await session.commit()

    session_factory = get_session_factory()

    async def _activate() -> GoalStatus | Exception:
        async with session_factory() as fresh:
            service = GoalService(
                repo=PostgresPerformanceRepository(fresh),
                scope=PostgresEmployeeScope(fresh),
                audit=PostgresAuditWriter(fresh),
                outbox=PostgresEventOutbox(fresh, clock=lambda: CLOCK),
            )
            try:
                row = await service.activate_goal(
                    identity(role=Role.EMPLOYEE), goal_id, CLOCK
                )
                await fresh.commit()
                return row.status
            except Exception as exc:  # noqa: BLE001 — the loser is asserted
                await fresh.rollback()
                return exc

    results = await asyncio.gather(_activate(), _activate())
    statuses = [result for result in results if isinstance(result, GoalStatus)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert statuses == [GoalStatus.ACTIVE]
    assert len(errors) == 1
    assert isinstance(errors[0], GoalNotDraftError)


async def test_concurrent_activate_and_cancel_have_one_winner_and_accurate_audit(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed source state is pinned: activate/cancel cannot both win.

    Without the `from_status` guard the cancel update could match the row after
    a concurrent activate committed, producing both transitions and an audit
    claiming `draft -> cancelled` for a row that was `active`. The conditional
    update now serializes them: exactly one transition and its audit record
    reflect the state the row actually had.
    """
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        goal_id = UUID((await _create_goal(employee)).json()["id"])

    await session.commit()
    session_factory = get_session_factory()

    async def _run(
        operation: str,
    ) -> tuple[str, GoalStatus | None, Exception | None]:
        async with session_factory() as fresh:
            service = GoalService(
                repo=PostgresPerformanceRepository(fresh),
                scope=PostgresEmployeeScope(fresh),
                audit=PostgresAuditWriter(fresh),
                outbox=PostgresEventOutbox(fresh, clock=lambda: CLOCK),
            )
            caller = identity(role=Role.EMPLOYEE)
            try:
                if operation == "activate":
                    row = await service.activate_goal(caller, goal_id, CLOCK)
                else:
                    row = await service.cancel_goal(caller, goal_id, CLOCK)
                await fresh.commit()
                return operation, row.status, None
            except Exception as exc:  # noqa: BLE001 — the loser is asserted
                await fresh.rollback()
                return operation, None, exc

    results = await asyncio.gather(_run("activate"), _run("cancel"))
    winners = [result for result in results if result[1] is not None]
    losers = [result for result in results if result[1] is None]
    assert len(winners) == 1
    assert len(losers) == 1
    winner, winning_status, _ = winners[0]
    losing_operation, _, losing_error = losers[0]

    if winner == "activate":
        assert winning_status is GoalStatus.ACTIVE
        assert losing_operation == "cancel"
        assert isinstance(losing_error, GoalTerminalError)
    else:
        assert winning_status is GoalStatus.CANCELLED
        assert losing_operation == "activate"
        assert isinstance(losing_error, GoalNotDraftError)

    async with session_factory() as check:
        stored = await PostgresPerformanceRepository(check).get_goal(TENANT_A, goal_id)
        audit = list(
            (
                await check.execute(
                    select(AuditEntry)
                    .where(
                        AuditEntry.entity_type == "goal",
                        AuditEntry.entity_id == goal_id,
                    )
                    .order_by(AuditEntry.id)
                )
            ).scalars()
        )

    assert stored is not None
    assert stored.status is winning_status
    actions = [row.action for row in audit]
    if winner == "activate":
        assert actions == ["goal.created", "goal.activated"]
    else:
        assert actions == ["goal.created", "goal.cancelled"]
        assert audit[-1].changes == {"status": {"old": "draft", "new": "cancelled"}}
