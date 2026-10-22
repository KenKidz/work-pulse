"""HTTP integration tests for overtime requests, transitions, audit and outbox (T5).

Every test drives the real router, domain service and PostgreSQL adapter
through the shared session. The router clock is injected (`overtime_api._now`),
so date rules never depend on wall time.

Covered here (`.ai/features/overtime.md` → T5):

- submit → `201 requested` + audit + valid `overtime.requested` envelope;
- approve → `manager_approved`, verify → `hr_verified` with their envelopes
  and notes stored but absent from events/audit values;
- reject from every open state with the right `rejected_from`, own withdraw;
- the pinned failure codes at the HTTP edge (future date, overlap, wrong state,
  not-open, `hours` bounds);
- list filters, inclusive `from`/`to` and clamped pagination;
- two concurrent decides: exactly one wins, the other gets the wrong-state 409.
"""

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import overtime as overtime_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.overtime import models as overtime_orm
from app.domains.overtime.exceptions import OvertimeNotRequestedError
from app.domains.overtime.schemas import (
    OvertimeDecision,
    OvertimeRequestStatus,
)
from app.domains.overtime.service import OvertimeService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.overtime import (
    PostgresOvertimeRepository,
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

CLOCK = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
TODAY = CLOCK.date()
HR_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000f1")

PINNED_REQUEST_KEYS = {
    "id",
    "tenant_id",
    "employee_id",
    "work_date",
    "hours",
    "status",
    "reason",
    "manager_note",
    "hr_note",
    "approved_by",
    "approved_at",
    "verified_by",
    "verified_at",
    "rejected_by",
    "rejected_at",
    "rejected_from",
    "withdrawn_at",
    "included_in_run_id",
    "created_at",
    "updated_at",
}

PII_REASON = "Late deployment — pii-reason"
PII_MANAGER_NOTE = "Approved verbally — pii-manager-note"
PII_HR_NOTE = "Verified by phone — pii-hr-note"


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


async def _seed_world(
    client_factory: ClientFactory, session: AsyncSession
) -> tuple[str, str, str]:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_employee(session, refs)
    return refs


async def _submit(
    client: AsyncClient,
    *,
    work_date: date = TODAY,
    hours: str = "2.50",
    reason: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "work_date": work_date.isoformat(),
        "hours": hours,
    }
    if reason is not None:
        body["reason"] = reason
    return await client.post("/api/v1/overtime/requests", json=body)


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "overtime_request")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _hr() -> Any:
    """A decider identity that is not the requester (never self-decision)."""
    return identity(role=Role.HR, employee_id=HR_EMPLOYEE)


async def test_submit_returns_201_requested_with_audit_and_event(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        response = await _submit(
            employee,
            work_date=date(2026, 10, 21),
            hours="2.50",
            reason=PII_REASON,
        )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == PINNED_REQUEST_KEYS
    assert body["tenant_id"] == str(TENANT_A)
    assert body["employee_id"] == str(ACTOR)
    assert body["work_date"] == "2026-10-21"
    assert body["hours"] == 2.5  # JSON number, never a formatted string
    assert body["status"] == "requested"
    assert body["reason"] == PII_REASON
    assert body["manager_note"] is None
    assert body["hr_note"] is None
    assert body["rejected_from"] is None
    assert body["included_in_run_id"] is None

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["overtime.requested"]
    assert audit[0].actor_id is not None
    changes = audit[0].changes or {}
    assert changes["status"] == {"old": None, "new": "requested"}
    assert changes["work_date"] == {"old": None, "new": "2026-10-21"}
    assert changes["hours"] == {"old": None, "new": "2.50"}
    assert changes["reason"] == "changed"  # PII marker, never the value
    assert PII_REASON not in str(changes)

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == ["overtime.requested"]
    payload = outbox[0].payload
    assert payload["version"] == 1
    assert payload["tenant_id"] == str(TENANT_A)
    assert set(payload["data"]) == {
        "overtime_id",
        "employee_id",
        "work_date",
        "hours",
    }
    assert payload["data"]["overtime_id"] == body["id"]
    assert payload["data"]["employee_id"] == str(ACTOR)
    assert payload["data"]["hours"] == 2.5
    assert PII_REASON not in str(payload)


async def test_approve_and_verify_store_notes_but_never_emit_them(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        submitted = await _submit(employee, work_date=date(2026, 10, 21))
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        approved = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/approve",
            json={"note": PII_MANAGER_NOTE},
        )
        verified = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/verify",
            json={"note": PII_HR_NOTE},
        )

    assert approved.status_code == 200
    approved_body = approved.json()
    assert approved_body["status"] == "manager_approved"
    assert approved_body["manager_note"] == PII_MANAGER_NOTE
    assert approved_body["approved_by"] == str(USER)
    assert approved_body["approved_at"] is not None

    assert verified.status_code == 200
    verified_body = verified.json()
    assert verified_body["status"] == "hr_verified"
    assert verified_body["hr_note"] == PII_HR_NOTE
    assert verified_body["verified_by"] is not None
    assert verified_body["verified_at"] is not None
    assert verified_body["hours"] == 2.5  # HR never adjusts the hours

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "overtime.requested",
        "overtime.approved",
        "overtime.verified",
    ]
    assert audit[1].changes is not None
    assert audit[1].changes["status"] == {
        "old": "requested",
        "new": "manager_approved",
    }
    assert audit[1].changes["manager_note"] == "changed"
    assert audit[2].changes is not None
    assert audit[2].changes["status"] == {
        "old": "manager_approved",
        "new": "hr_verified",
    }
    assert audit[2].changes["hr_note"] == "changed"
    encoded = str([row.changes for row in audit])
    assert PII_MANAGER_NOTE not in encoded
    assert PII_HR_NOTE not in encoded

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "overtime.requested",
        "overtime.approved",
        "overtime.verified",
    ]
    assert set(outbox[1].payload["data"]) == {
        "overtime_id",
        "employee_id",
        "work_date",
        "hours",
        "approved_by",
    }
    assert set(outbox[2].payload["data"]) == {
        "overtime_id",
        "employee_id",
        "work_date",
        "hours",
        "verified_by",
    }
    encoded = str([row.payload for row in outbox])
    assert PII_MANAGER_NOTE not in encoded
    assert PII_HR_NOTE not in encoded


@pytest.mark.parametrize(
    "source_state",
    [
        OvertimeRequestStatus.REQUESTED,
        OvertimeRequestStatus.MANAGER_APPROVED,
        OvertimeRequestStatus.HR_VERIFIED,
    ],
)
async def test_reject_records_the_observed_source_state(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    source_state: OvertimeRequestStatus,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        submitted = await _submit(employee, work_date=date(2026, 10, 21))
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        if source_state in (
            OvertimeRequestStatus.MANAGER_APPROVED,
            OvertimeRequestStatus.HR_VERIFIED,
        ):
            assert (
                await hr.post(
                    f"/api/v1/overtime/requests/{request_id}/approve", json={}
                )
            ).status_code == 200
        if source_state is OvertimeRequestStatus.HR_VERIFIED:
            assert (
                await hr.post(f"/api/v1/overtime/requests/{request_id}/verify", json={})
            ).status_code == 200
        rejected = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/reject",
            json={"note": "Rejected — pii-reject-note"},
        )

    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["rejected_from"] == source_state.value
    assert body["rejected_by"] is not None
    assert body["rejected_at"] is not None
    if source_state is OvertimeRequestStatus.REQUESTED:
        assert body["manager_note"] == "Rejected — pii-reject-note"
        assert body["hr_note"] is None
    else:
        assert body["hr_note"] == "Rejected — pii-reject-note"
        assert body["manager_note"] is None

    outbox = await _outbox_rows(session)
    assert outbox[-1].event_type == "overtime.rejected"
    data = outbox[-1].payload["data"]
    assert data["rejected_from"] == source_state.value
    assert set(data) == {
        "overtime_id",
        "employee_id",
        "work_date",
        "hours",
        "rejected_by",
        "rejected_from",
    }
    assert "pii-reject-note" not in str(outbox[-1].payload)


async def test_withdraw_own_open_request_emits_the_event(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        submitted = await _submit(employee, work_date=date(2026, 10, 20))
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        assert (
            await hr.post(f"/api/v1/overtime/requests/{request_id}/approve", json={})
        ).status_code == 200

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        response = await employee.post(
            f"/api/v1/overtime/requests/{request_id}/withdraw"
        )

    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert response.json()["withdrawn_at"] is not None

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "overtime.requested",
        "overtime.approved",
        "overtime.withdrawn",
    ]
    outbox = await _outbox_rows(session)
    assert outbox[-1].event_type == "overtime.withdrawn"
    assert set(outbox[-1].payload["data"]) == {
        "overtime_id",
        "employee_id",
        "work_date",
    }


async def test_open_overlap_blocks_and_terminal_rows_never_do(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    work_date = date(2026, 10, 19)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        first = await _submit(employee, work_date=work_date)
        overlap = await _submit(employee, work_date=work_date)
    assert first.status_code == 201
    _assert_error(overlap, 409, "overtime_overlaps")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        withdrawn = await employee.post(
            f"/api/v1/overtime/requests/{first.json()['id']}/withdraw"
        )
        assert withdrawn.status_code == 200
        after_withdrawn = await _submit(employee, work_date=work_date)
    assert after_withdrawn.status_code == 201

    # An included row is counted separately and must not block a new claim.
    await session.execute(
        update(overtime_orm.OvertimeRequest)
        .where(overtime_orm.OvertimeRequest.id == UUID(after_withdrawn.json()["id"]))
        .values(status="included_in_payroll", included_in_run_id=UUID(int=7))
    )
    await session.flush()

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        after_included = await _submit(employee, work_date=work_date)
    assert after_included.status_code == 201


async def test_wrong_states_and_hours_bounds_are_pinned_errors(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        future = await _submit(employee, work_date=date(2026, 10, 23))
        zero = await _submit(employee, work_date=date(2026, 10, 21), hours="0")
        too_many = await _submit(employee, work_date=date(2026, 10, 21), hours="12.01")
        too_precise = await _submit(
            employee, work_date=date(2026, 10, 21), hours="1.234"
        )
        long_reason = await _submit(
            employee,
            work_date=date(2026, 10, 21),
            reason="x" * 501,
        )
        submitted = await _submit(employee, work_date=date(2026, 10, 21))

    _assert_error(future, 409, "overtime_work_date_in_future")
    assert zero.status_code == 422
    assert too_many.status_code == 422
    assert too_precise.status_code == 422
    assert long_reason.status_code == 422
    request_id = submitted.json()["id"]

    async with client_factory(_hr()) as hr:
        wrong_verify = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/verify", json={}
        )
        first_approve = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/approve", json={}
        )
        second_approve = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/approve", json={}
        )
        long_note = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/reject",
            json={"note": "x" * 501},
        )
        verified = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/verify", json={}
        )

    _assert_error(wrong_verify, 409, "overtime_not_manager_approved")
    assert first_approve.status_code == 200
    _assert_error(second_approve, 409, "overtime_not_requested")
    assert long_note.status_code == 422
    assert verified.status_code == 200

    # Withdraw is own-only and pre-verification: the requester cannot withdraw
    # an HR-verified (payroll-bound) row.
    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        withdrawn = await employee.post(
            f"/api/v1/overtime/requests/{request_id}/withdraw"
        )
    _assert_error(withdrawn, 409, "overtime_not_open")

    # HR may reject the verified row (the ADR 0018 un-verify window); after
    # that the row is terminal and both reject and withdraw are not-open.
    async with client_factory(_hr()) as hr:
        unverified = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/reject", json={}
        )
        reject_terminal = await hr.post(
            f"/api/v1/overtime/requests/{request_id}/reject", json={}
        )
    assert unverified.status_code == 200
    assert unverified.json()["rejected_from"] == "hr_verified"
    _assert_error(reject_terminal, 409, "overtime_not_open")

    # Included rows are payroll's terminal accounting fact: neither the
    # requester nor HR can withdraw or reject them.
    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        included = await _submit(employee, work_date=date(2026, 10, 20))
    included_id = UUID(included.json()["id"])
    await session.execute(
        update(overtime_orm.OvertimeRequest)
        .where(overtime_orm.OvertimeRequest.id == included_id)
        .values(status="included_in_payroll", included_in_run_id=UUID(int=8))
    )
    await session.flush()

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        withdraw_included = await employee.post(
            f"/api/v1/overtime/requests/{included_id}/withdraw"
        )
    async with client_factory(_hr()) as hr:
        reject_included = await hr.post(
            f"/api/v1/overtime/requests/{included_id}/reject", json={}
        )
    _assert_error(withdraw_included, 409, "overtime_not_open")
    _assert_error(reject_included, 409, "overtime_not_open")


async def test_list_filters_and_clamped_pagination(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        earlier = await _submit(employee, work_date=date(2026, 10, 19))
        later = await _submit(employee, work_date=date(2026, 10, 21))
        assert earlier.status_code == 201 and later.status_code == 201

        listed = await employee.get("/api/v1/overtime/requests")
        by_status = await employee.get(
            "/api/v1/overtime/requests", params={"status": "requested"}
        )
        window = await employee.get(
            "/api/v1/overtime/requests",
            params={"from": "2026-10-21", "to": "2026-10-21"},
        )
        clamped = await employee.get(
            "/api/v1/overtime/requests", params={"page": 0, "page_size": 1000}
        )
        foreign_filter = await employee.get(
            "/api/v1/overtime/requests", params={"employee_id": str(UUID(int=9))}
        )
        detail = await employee.get(f"/api/v1/overtime/requests/{earlier.json()['id']}")

    assert listed.status_code == 200
    body = listed.json()
    assert set(body["page"]) == {"page", "page_size", "total_items", "total_pages"}
    assert body["page"]["total_items"] == 2
    assert {item["id"] for item in body["items"]} == {
        earlier.json()["id"],
        later.json()["id"],
    }
    assert len(by_status.json()["items"]) == 2
    assert [item["id"] for item in window.json()["items"]] == [later.json()["id"]]
    assert clamped.json()["page"] == {
        "page": 1,
        "page_size": 100,
        "total_items": 2,
        "total_pages": 1,
    }
    _assert_error(foreign_filter, 404, "employee_not_found")
    assert detail.status_code == 200
    assert detail.json()["id"] == earlier.json()["id"]


async def test_concurrent_decides_have_exactly_one_winner(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(overtime_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        submitted = await _submit(employee, work_date=date(2026, 10, 21))
    request_id = UUID(submitted.json()["id"])

    # The fresh sessions in the race must see the committed setup rows.
    await session.commit()

    session_factory = get_session_factory()

    async def _approve() -> OvertimeRequestStatus | Exception:
        async with session_factory() as fresh:
            service = OvertimeService(
                repo=PostgresOvertimeRepository(fresh),
                audit=PostgresAuditWriter(fresh),
                outbox=PostgresEventOutbox(fresh, clock=lambda: CLOCK),
            )
            try:
                row = await service.approve(
                    _hr(),
                    request_id,
                    OvertimeDecision(),
                    CLOCK,
                )
                await fresh.commit()
                return row.status
            except Exception as exc:  # noqa: BLE001 — the loser is asserted
                await fresh.rollback()
                return exc

    results = await asyncio.gather(_approve(), _approve())
    statuses = [
        result for result in results if isinstance(result, OvertimeRequestStatus)
    ]
    errors = [result for result in results if isinstance(result, Exception)]
    assert statuses == [OvertimeRequestStatus.MANAGER_APPROVED]
    assert len(errors) == 1
    assert isinstance(errors[0], OvertimeNotRequestedError)

    async with get_session_factory()() as check:
        row = (
            await check.execute(
                select(overtime_orm.OvertimeRequest).where(
                    overtime_orm.OvertimeRequest.id == request_id
                )
            )
        ).scalar_one()
        assert row.status == "manager_approved"
