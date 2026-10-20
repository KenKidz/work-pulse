"""HTTP integration tests for leave requests, transitions, audit and outbox (T6).

Every test drives the real routers, domain service and PostgreSQL adapter
through the shared session. The router clock is injected (`leave_api._now`),
so date rules never depend on wall time.

Covered here (`.ai/features/leave.md` → T6):

- submit → `201 PENDING`, inclusive `days`, balance untouched, audit + outbox;
- approve → `APPROVED`, balance deducted, audit + `leave.approved` envelope;
- reject/withdraw → terminal status, balance untouched, their envelopes;
- the pinned failure codes at the HTTP edge (overlap, non-pending, balance,
  backdating, terminated employee);
- `unpaid` bypasses balances and never creates a balance row;
- list filters use inclusive overlap bounds and stable pagination.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import leave as leave_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.leave.schemas import LeaveTypeCode
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
TODAY = CLOCK.date()
HR_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000f1")

PINNED_REQUEST_KEYS = {
    "id",
    "tenant_id",
    "employee_id",
    "leave_type_id",
    "leave_type_code",
    "start_date",
    "end_date",
    "days",
    "status",
    "reason",
    "decision_note",
    "decided_by",
    "decided_at",
    "created_at",
    "updated_at",
}

PII_REASON = "Family trip — pii-reason"
PII_NOTE = "Approved verbally — pii-note"


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
    manager_id: UUID | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        employee_id,
        employee_code=code,
        first_name="Actor",
        last_name="Self",
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


async def _types(client: AsyncClient) -> dict[LeaveTypeCode, dict[str, Any]]:
    response = await client.get("/api/v1/leave/types")
    assert response.status_code == 200
    return {LeaveTypeCode(row["code"]): row for row in response.json()}


async def _submit(
    client: AsyncClient,
    leave_type_id: str,
    *,
    start_date: date = TODAY,
    end_date: date | None = None,
    reason: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "leave_type_id": leave_type_id,
        "start_date": start_date.isoformat(),
        "end_date": (end_date or start_date).isoformat(),
    }
    if reason is not None:
        body["reason"] = reason
    return await client.post("/api/v1/leave/requests", json=body)


async def _balances(
    client: AsyncClient, *, employee_id: UUID = ACTOR, year: int = 2026
) -> list[dict[str, Any]]:
    response = await client.get(
        "/api/v1/leave/balances",
        params={"employee_id": str(employee_id), "year": year},
    )
    assert response.status_code == 200
    return response.json()


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "leave_request")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _hr() -> Any:
    """A decider identity that is not the requester (never self-approval)."""
    return identity(role=Role.HR, employee_id=HR_EMPLOYEE)


async def test_submit_returns_201_pending_with_inclusive_days_and_untouched_balance(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        response = await _submit(
            employee,
            types[LeaveTypeCode.ANNUAL]["id"],
            start_date=date(2026, 10, 19),
            end_date=date(2026, 10, 21),
            reason=PII_REASON,
        )
        balances = await _balances(employee)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == PINNED_REQUEST_KEYS
    assert body["tenant_id"] == str(TENANT_A)
    assert body["employee_id"] == str(ACTOR)
    assert body["leave_type_code"] == "annual"
    assert body["start_date"] == "2026-10-19"
    assert body["end_date"] == "2026-10-21"
    assert body["days"] == 3  # inclusive whole calendar days
    assert body["status"] == "pending"
    assert body["reason"] == PII_REASON
    assert body["decision_note"] is None
    assert body["decided_by"] is None
    assert body["decided_at"] is None

    # Submission never deducts: the lazily created balance is untouched.
    assert len(balances) == 1
    assert balances[0]["entitlement_days"] == 12
    assert balances[0]["used_days"] == 0
    assert balances[0]["remaining_days"] == 12

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["leave.requested"]
    assert audit[0].actor_id is not None
    changes = audit[0].changes or {}
    assert changes["status"] == {"old": None, "new": "pending"}
    assert changes["reason"] == "changed"  # PII marker, never the value
    assert PII_REASON not in str(changes)

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == ["leave.requested"]
    payload = outbox[0].payload
    assert payload["version"] == 1
    assert payload["tenant_id"] == str(TENANT_A)
    assert set(payload["data"]) == {
        "leave_request_id",
        "employee_id",
        "leave_type_code",
        "start_date",
        "end_date",
        "days",
    }
    assert payload["data"]["leave_request_id"] == body["id"]
    assert payload["data"]["employee_id"] == str(ACTOR)
    assert PII_REASON not in str(payload)


async def test_approve_deducts_balance_writes_audit_and_envelope(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        submitted = await _submit(
            employee,
            types[LeaveTypeCode.ANNUAL]["id"],
            start_date=date(2026, 10, 19),
            end_date=date(2026, 10, 21),
        )
    assert submitted.status_code == 201
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        response = await hr.post(
            f"/api/v1/leave/requests/{request_id}/approve",
            json={"decision_note": PII_NOTE},
        )
    assert response.status_code == 200
    approved = response.json()
    assert approved["status"] == "approved"
    assert approved["decision_note"] == PII_NOTE
    assert approved["decided_at"] is not None

    async with client_factory() as admin:
        balances = await _balances(admin)

    assert len(balances) == 1
    assert balances[0]["used_days"] == 3
    assert balances[0]["remaining_days"] == 9

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["leave.requested", "leave.approved"]
    changes = audit[-1].changes or {}
    assert changes["status"] == {"old": "pending", "new": "approved"}
    assert changes["decision_note"] == "changed"  # PII marker, never the value
    assert PII_NOTE not in str(changes)

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "leave.requested",
        "leave.approved",
    ]
    data = outbox[-1].payload["data"]
    assert data["leave_request_id"] == request_id
    assert data["employee_id"] == str(ACTOR)
    assert data["leave_type_code"] == "annual"
    assert data["start_date"] == "2026-10-19"
    assert data["end_date"] == "2026-10-21"
    assert data["days"] == 3
    assert data["decided_by"] == str(USER)
    # The envelope carries the decision actor's *user* id, never a note.
    assert outbox[-1].payload["actor_id"] == str(USER)
    assert PII_NOTE not in str(outbox[-1].payload)


async def test_reject_leaves_the_balance_untouched_and_emits_the_event(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        submitted = await _submit(
            employee,
            types[LeaveTypeCode.SICK]["id"],
            start_date=date(2026, 10, 19),
            end_date=date(2026, 10, 20),
        )
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        response = await hr.post(f"/api/v1/leave/requests/{request_id}/reject", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    async with client_factory() as admin:
        balances = await _balances(admin)
    assert len(balances) == 1
    assert balances[0]["used_days"] == 0  # rejection restores nothing, spends nothing

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == ["leave.requested", "leave.rejected"]
    rejected = outbox[-1].payload["data"]
    assert rejected["leave_request_id"] == request_id
    assert "days" not in rejected  # pinned payload: no deduction on reject


async def test_withdraw_own_pending_request_emits_the_event(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        submitted = await _submit(
            employee, types[LeaveTypeCode.ANNUAL]["id"], reason=PII_REASON
        )
        request_id = submitted.json()["id"]
        response = await employee.post(f"/api/v1/leave/requests/{request_id}/withdraw")

    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"

    async with client_factory() as admin:
        balances = await _balances(admin)
    assert balances[0]["used_days"] == 0  # nothing was deducted before approval

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["leave.requested", "leave.withdrawn"]
    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "leave.requested",
        "leave.withdrawn",
    ]
    withdrawn = outbox[-1].payload["data"]
    assert set(withdrawn) == {
        "leave_request_id",
        "employee_id",
        "start_date",
        "end_date",
    }


async def test_overlap_is_409_and_rejected_ranges_never_block(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        annual = types[LeaveTypeCode.ANNUAL]["id"]
        first = await _submit(
            employee, annual, start_date=date(2026, 10, 20), end_date=date(2026, 10, 22)
        )
        assert first.status_code == 201

        overlap = await _submit(
            employee, annual, start_date=date(2026, 10, 22), end_date=date(2026, 10, 24)
        )
        adjacent = await _submit(
            employee, annual, start_date=date(2026, 10, 23), end_date=date(2026, 10, 24)
        )

    _assert_error(overlap, 409, "leave_overlaps")
    assert adjacent.status_code == 201  # inclusive endpoints, no intersection

    # A rejected range no longer blocks its dates.
    async with client_factory(_hr()) as hr:
        rejected = await hr.post(
            f"/api/v1/leave/requests/{first.json()['id']}/reject", json={}
        )
    assert rejected.status_code == 200

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        again = await _submit(
            employee, annual, start_date=date(2026, 10, 20), end_date=date(2026, 10, 21)
        )
    assert again.status_code == 201


async def test_non_pending_transition_is_409(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        submitted = await _submit(employee, types[LeaveTypeCode.ANNUAL]["id"])
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        assert (
            await hr.post(f"/api/v1/leave/requests/{request_id}/approve", json={})
        ).status_code == 200
        second = await hr.post(f"/api/v1/leave/requests/{request_id}/approve", json={})
        rejected = await hr.post(f"/api/v1/leave/requests/{request_id}/reject", json={})

    _assert_error(second, 409, "leave_not_pending")
    _assert_error(rejected, 409, "leave_not_pending")


async def test_balance_rules_at_the_http_edge(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        annual = types[LeaveTypeCode.ANNUAL]["id"]
        # 13 calendar days > annual's 12-day fixture entitlement.
        too_long = await _submit(
            employee, annual, start_date=date(2026, 10, 19), end_date=date(2026, 10, 31)
        )
        # 12 days exactly is allowed and leaves a zero balance.
        full = await _submit(
            employee, annual, start_date=date(2026, 11, 1), end_date=date(2026, 11, 12)
        )
    _assert_error(too_long, 409, "leave_insufficient_balance")
    assert full.status_code == 201

    # Approval spends the whole entitlement; the next day is then refused.
    async with client_factory(_hr()) as hr:
        approved = await hr.post(
            f"/api/v1/leave/requests/{full.json()['id']}/approve", json={}
        )
    assert approved.status_code == 200

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        exhausted = await _submit(
            employee, annual, start_date=date(2026, 11, 13), end_date=date(2026, 11, 13)
        )
        balances = await _balances(employee)

    _assert_error(exhausted, 409, "leave_insufficient_balance")
    assert balances[0]["used_days"] == 12
    assert balances[0]["remaining_days"] == 0


async def test_unpaid_bypasses_balances_entirely(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        response = await _submit(
            employee,
            types[LeaveTypeCode.UNPAID]["id"],
            start_date=date(2026, 10, 19),
            end_date=date(2026, 11, 17),  # 30 days, far beyond annual's 12
        )
        balances = await _balances(employee)

    assert response.status_code == 201
    assert response.json()["days"] == 30
    assert balances == []  # no balance row is ever created for unpaid


async def test_start_date_rules_use_the_injected_clock(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        annual_yesterday = await _submit(
            employee,
            types[LeaveTypeCode.ANNUAL]["id"],
            start_date=date(2026, 10, 18),
        )
        sick_too_old = await _submit(
            employee,
            types[LeaveTypeCode.SICK]["id"],
            start_date=date(2026, 10, 11),  # 8 days back; the window is 7
        )
        sick_boundary = await _submit(
            employee,
            types[LeaveTypeCode.SICK]["id"],
            start_date=date(2026, 10, 12),  # exactly 7 days back
        )

    _assert_error(annual_yesterday, 409, "leave_start_too_old")
    _assert_error(sick_too_old, 409, "leave_start_too_old")
    assert sick_boundary.status_code == 201


async def test_terminated_employee_cannot_submit(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    terminated = UUID("00000000-0000-4000-8000-0000000000e1")
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(
        session,
        refs,
        employee_id=terminated,
        code="EMP-00010",
        email="terminated@example.test",
        status=EmployeeStatus.TERMINATED,
    )

    async with client_factory(identity(employee_id=terminated)) as employee:
        types = await _types(employee)
        response = await _submit(employee, types[LeaveTypeCode.UNPAID]["id"])

    _assert_error(response, 409, "employee_not_active")


async def test_list_filters_and_stable_pagination(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(leave_api, "_now", lambda: CLOCK)
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        types = await _types(employee)
        annual = types[LeaveTypeCode.ANNUAL]["id"]
        earlier = await _submit(
            employee, annual, start_date=date(2026, 10, 19), end_date=date(2026, 10, 19)
        )
        later = await _submit(
            employee, annual, start_date=date(2026, 10, 26), end_date=date(2026, 10, 27)
        )
        assert earlier.status_code == 201 and later.status_code == 201

        listed = await employee.get("/api/v1/leave/requests")
        by_status = await employee.get(
            "/api/v1/leave/requests", params={"status": "pending"}
        )
        window = await employee.get(
            "/api/v1/leave/requests",
            params={"from": "2026-10-26", "to": "2026-10-26"},
        )
        detail = await employee.get(f"/api/v1/leave/requests/{earlier.json()['id']}")

    assert listed.status_code == 200
    body = listed.json()
    assert set(body["page"]) == {"page", "page_size", "total_items", "total_pages"}
    assert body["page"]["total_items"] == 2
    assert [item["id"] for item in body["items"]] == [
        later.json()["id"],
        earlier.json()["id"],
    ]
    assert len(by_status.json()["items"]) == 2
    # `from`/`to` are inclusive overlap bounds: a range starting inside matches.
    assert [item["id"] for item in window.json()["items"]] == [later.json()["id"]]
    assert detail.status_code == 200
    assert detail.json()["id"] == earlier.json()["id"]
