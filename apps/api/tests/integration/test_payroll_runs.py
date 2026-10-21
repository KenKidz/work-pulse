"""HTTP integration tests for the payroll run lifecycle (T6).

Drives the real router → service → Postgres adapter. The overtime/attendance/
leave seam ports are deterministic fakes (`payroll_seams`): the overtime
feature has not been built yet (P0). Covered:

- closed-period and one-open-run guards (`409`);
- finalize only from `calculated`, exactly-one-wins and immutability (`409`);
- bonus guards (`payroll_employee_not_in_run`, finalized run);
- HR view-only, employee/manager denied (`403`), tenant isolation (`404`);
- correction-run supersession and audit entries;
- `payroll.calculated` + `payslip.issued` envelopes in the outbox, validated
  against their v1 contracts with no amounts.
"""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from jsonschema import Draft202012Validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.payroll import models as payroll_orm
from app.domains.payroll.exceptions import PayrollRunStaleError
from app.domains.payroll.schemas import PayrollRunStatus
from app.domains.payroll.service import PayrollService
from app.infrastructure.postgres.audit import PostgresAuditWriter
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.outbox import PostgresEventOutbox
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.payroll import (
    PostgresPayrollRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    identity,
)
from tests.unit.payroll.fakes import FakeOvertimeHoursPort

PERIOD = "2026-09"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OVERTIME_ID = UUID("00000000-0000-4000-8000-0000000000f1")

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMAS_DIR = REPO_ROOT / "contracts" / "events" / "payroll"
_AMOUNT_MARKERS = (
    "amount",
    "base",
    "bonus",
    "deduction",
    "gross",
    "net",
    "rate",
    "currency",
    "salary",
    "totals",
)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def _seed_actor(session: AsyncSession, refs: tuple[str, str, str]) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
        ACTOR,
        employee_code="EMP-00009",
        first_name="Actor",
        last_name="Self",
        email="self@example.test",
        phone=None,
        department_id=UUID(refs[0]),
        position_id=UUID(refs[1]),
        location_id=UUID(refs[2]),
        manager_id=None,
        employment_type=EmploymentType.FULL_TIME,
        status=EmployeeStatus.ACTIVE,
        hired_at=date(2026, 1, 5),
    )


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    await _seed_actor(session, refs)


async def _create_salary(
    client: AsyncClient,
    *,
    amount: str = "1000.00",
) -> None:
    response = await client.post(
        "/api/v1/payroll/salaries",
        json={
            "employee_id": str(ACTOR),
            "amount": amount,
            "effective_from": "2026-01-01",
        },
    )
    assert response.status_code == 201, response.text


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.action == action)
        .order_by(AuditEntry.occurred_at, AuditEntry.id)
    )
    return list(result.scalars().all())


def _matching_keys(payload: Any, markers: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if any(marker in key.lower() for marker in markers):
                found.append(key)
            found.extend(_matching_keys(value, markers))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_matching_keys(item, markers))
    return found


async def _payslip_statuses(session: AsyncSession, run_id: UUID) -> list[str]:
    rows = (
        await session.execute(
            select(payroll_orm.Payslip)
            .where(
                payroll_orm.Payslip.tenant_id == TENANT_A,
                payroll_orm.Payslip.run_id == run_id,
            )
            .order_by(payroll_orm.Payslip.employee_id)
        )
    ).scalars()
    return [row.status for row in rows]


# --- create guards --------------------------------------------------------


async def test_create_run_returns_queued_and_audits(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        response = await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["period"] == PERIOD
    assert body["employee_count"] == 0
    assert body["totals"] is None

    entries = await _audit_rows(session, "payroll.run_created")
    assert len(entries) == 1
    assert entries[0].entity_type == "payroll_run"
    assert entries[0].changes is not None
    assert entries[0].changes["status"] == {"old": None, "new": "queued"}


async def test_create_run_for_an_open_month_is_rejected(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        response = await admin.post("/api/v1/payroll/runs", json={"period": "2099-01"})

    _assert_error(response, 409, "payroll_period_not_closed")


async def test_second_open_run_conflicts(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        first = await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})
        second = await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})

    assert first.status_code == 201
    _assert_error(second, 409, "payroll_period_open")


async def test_finalize_requires_a_calculated_run(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()
        response = await admin.post(f"/api/v1/payroll/runs/{run['id']}/finalize")

    _assert_error(response, 409, "payroll_run_not_calculated")


# --- finalize -------------------------------------------------------------


async def test_finalize_issues_payslips_emits_events_and_is_immutable(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    overtime, attendance, leave_days = payroll_seams
    await _seed_world(client_factory, session)
    attendance.worked[(TENANT_A, PERIOD, ACTOR)] = Decimal("80.00")
    attendance.days[(TENANT_A, PERIOD, ACTOR)] = 10
    leave_days.days[(TENANT_A, PERIOD, ACTOR)] = 1
    overtime.add(
        TENANT_A,
        PERIOD,
        overtime_id=OVERTIME_ID,
        employee_id=ACTOR,
        hours=Decimal("5.00"),
    )

    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()
        calculated = await payroll_service.calculate_run(run["id"], NOW)
        assert calculated.status is PayrollRunStatus.CALCULATED

        response = await admin.post(f"/api/v1/payroll/runs/{run['id']}/finalize")
        assert response.status_code == 200
        assert response.json()["status"] == "finalized"

        # Finalized is terminal: every mutation is rejected with the pinned code.
        recalculate = await admin.post(f"/api/v1/payroll/runs/{run['id']}/recalculate")
        _assert_error(recalculate, 409, "payroll_run_finalized")
        bonus = await admin.post(
            f"/api/v1/payroll/runs/{run['id']}/bonuses",
            json={"employee_id": str(ACTOR), "amount": "10.00"},
        )
        _assert_error(bonus, 409, "payroll_run_finalized")

    assert await _payslip_statuses(session, UUID(run["id"])) == ["issued"]
    assert overtime.included == {OVERTIME_ID}

    outbox_rows = list(
        (
            await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
        ).scalars()
    )
    by_type = {row.event_type: row for row in outbox_rows}
    assert set(by_type) == {"payroll.calculated", "payslip.issued"}

    for schema_name, event_type in (
        ("payroll-calculated.v1.json", "payroll.calculated"),
        ("payslip-issued.v1.json", "payslip.issued"),
    ):
        schema = json.loads((SCHEMAS_DIR / schema_name).read_text())
        Draft202012Validator(schema).validate(by_type[event_type].payload["data"])
    for row in outbox_rows:
        assert _matching_keys(row.payload["data"], _AMOUNT_MARKERS) == []

    finalized_audit = await _audit_rows(session, "payroll.run_finalized")
    assert len(finalized_audit) == 1
    assert finalized_audit[0].changes is not None
    assert finalized_audit[0].changes["status"] == {
        "old": "calculated",
        "new": "finalized",
    }


async def test_recalculate_requeues_a_calculated_run(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()
        await payroll_service.calculate_run(run["id"], NOW)

        response = await admin.post(f"/api/v1/payroll/runs/{run['id']}/recalculate")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["totals"] is None
    assert body["employee_count"] == 0

    entries = await _audit_rows(session, "payroll.run_recalculated")
    assert len(entries) == 1
    assert entries[0].changes is not None
    assert entries[0].changes["status"] == {
        "old": "calculated",
        "new": "queued",
    }


async def test_denied_roles_are_403_and_not_found_is_404(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()

    async with client_factory(identity(role=Role.HR)) as hr:
        _assert_error(
            await hr.post(f"/api/v1/payroll/runs/{run['id']}/finalize"),
            403,
            "forbidden",
        )
        _assert_error(
            await hr.post("/api/v1/payroll/runs", json={"period": PERIOD}),
            403,
            "forbidden",
        )

    for role in (Role.EMPLOYEE, Role.MANAGER):
        async with client_factory(identity(role=role)) as denied:
            _assert_error(
                await denied.post("/api/v1/payroll/runs", json={"period": PERIOD}),
                403,
                "forbidden",
            )

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        _assert_error(
            await tenant_b.post(f"/api/v1/payroll/runs/{run['id']}/finalize"),
            404,
            "payroll_run_not_found",
        )
        _assert_error(
            await tenant_b.post(f"/api/v1/payroll/runs/{run['id']}/recalculate"),
            404,
            "payroll_run_not_found",
        )


async def test_bonus_requires_a_calculated_employee_and_never_audits_amounts(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()

        early = await admin.post(
            f"/api/v1/payroll/runs/{run['id']}/bonuses",
            json={"employee_id": str(ACTOR), "amount": "50.00"},
        )
        _assert_error(early, 409, "payroll_employee_not_in_run")

        await payroll_service.calculate_run(run["id"], NOW)

        created = await admin.post(
            f"/api/v1/payroll/runs/{run['id']}/bonuses",
            json={
                "employee_id": str(ACTOR),
                "amount": "50.00",
                "note": "Q3 retention",
            },
        )
        assert created.status_code == 201
        assert created.json()["amount"] == 50.0

    entries = await _audit_rows(session, "payroll.bonus_created")
    assert len(entries) == 1
    assert entries[0].changes is not None
    assert entries[0].changes["amount"] == "changed"
    assert entries[0].changes["note"] == "changed"
    encoded = json.dumps(entries[0].changes)
    assert "50.00" not in encoded
    assert "Q3 retention" not in encoded


# --- recalculation and supersession ---------------------------------------


async def test_correction_run_supersedes_the_prior_finalized_run(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        first = (
            await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})
        ).json()
        await payroll_service.calculate_run(first["id"], NOW)
        assert (
            await admin.post(f"/api/v1/payroll/runs/{first['id']}/finalize")
        ).status_code == 200

        correction = (
            await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})
        ).json()
        assert correction["supersedes_run_id"] == first["id"]
        await payroll_service.calculate_run(correction["id"], NOW)
        assert (
            await admin.post(f"/api/v1/payroll/runs/{correction['id']}/finalize")
        ).status_code == 200

    assert await _payslip_statuses(session, UUID(first["id"])) == ["superseded"]
    assert await _payslip_statuses(session, UUID(correction["id"])) == ["issued"]

    first_run = (
        await session.execute(
            select(payroll_orm.PayrollRun).where(
                payroll_orm.PayrollRun.id == UUID(first["id"])
            )
        )
    ).scalar_one()
    assert first_run.superseded_by_run_id == UUID(correction["id"])
    assert len(await _audit_rows(session, "payroll.run_superseded")) == 1


async def test_finalize_rolls_back_when_the_overtime_flip_is_short(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    overtime, attendance, leave_days = payroll_seams
    await _seed_world(client_factory, session)
    overtime.add(
        TENANT_A,
        PERIOD,
        overtime_id=OVERTIME_ID,
        employee_id=ACTOR,
        hours=Decimal("5.00"),
    )

    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()
        await payroll_service.calculate_run(run["id"], NOW)

    # The fresh session must see committed setup rows.
    await session.commit()

    short_flip = FakeOvertimeHoursPort()
    short_flip.flip_limit = 0
    session_factory = get_session_factory()
    run_id = UUID(run["id"])

    async with session_factory() as fresh:
        service = PayrollService(
            repo=PostgresPayrollRepository(fresh),
            audit=PostgresAuditWriter(fresh),
            outbox=PostgresEventOutbox(fresh, clock=lambda: NOW),
            overtime=short_flip,
            attendance=attendance,
            leave_days=leave_days,
        )
        with pytest.raises(PayrollRunStaleError):
            await service.finalize_run(identity(), run_id, NOW)
        await fresh.rollback()

    async with session_factory() as check:
        run_row = (
            await check.execute(
                select(payroll_orm.PayrollRun).where(
                    payroll_orm.PayrollRun.id == run_id
                )
            )
        ).scalar_one()
        assert run_row.status == "calculated"
        payslips = list(
            (
                await check.execute(
                    select(payroll_orm.Payslip).where(
                        payroll_orm.Payslip.run_id == run_id
                    )
                )
            ).scalars()
        )
        assert [row.status for row in payslips] == ["provisional"]
        issued_events = list(
            (
                await check.execute(
                    select(EventOutboxRow).where(
                        EventOutboxRow.event_type == "payslip.issued"
                    )
                )
            ).scalars()
        )
        assert issued_events == []


async def test_concurrent_finalize_has_exactly_one_winner(
    client_factory: ClientFactory,
    session: AsyncSession,
    payroll_service: PayrollService,
    payroll_seams,
) -> None:
    await _seed_world(client_factory, session)
    async with client_factory() as admin:
        await _create_salary(admin)
        run = (await admin.post("/api/v1/payroll/runs", json={"period": PERIOD})).json()
        await payroll_service.calculate_run(run["id"], NOW)

    # The fresh sessions in the race must see committed setup rows.
    await session.commit()

    run_id = UUID(run["id"])
    session_factory = get_session_factory()
    overtime, attendance, leave_days = payroll_seams

    # Hold the row lock so both attempts pass the status pre-check and queue
    # on `transition_run`'s SELECT … FOR UPDATE; then release and let them race.
    blocker = session_factory()
    await blocker.execute(
        text("SELECT id FROM payroll_runs WHERE id = :id FOR UPDATE"),
        {"id": run_id},
    )

    async def _finalize() -> PayrollRunStatus | Exception:
        async with session_factory() as fresh:
            service = PayrollService(
                repo=PostgresPayrollRepository(fresh),
                audit=PostgresAuditWriter(fresh),
                outbox=PostgresEventOutbox(fresh, clock=lambda: NOW),
                overtime=overtime,
                attendance=attendance,
                leave_days=leave_days,
            )
            try:
                result = await service.finalize_run(identity(), run_id, NOW)
                await fresh.commit()
                return result.status
            except Exception as exc:  # noqa: BLE001 — the loser is asserted
                await fresh.rollback()
                return exc

    first = asyncio.create_task(_finalize())
    second = asyncio.create_task(_finalize())
    await asyncio.sleep(0.1)
    await blocker.commit()
    await blocker.close()

    results = await asyncio.gather(first, second)
    statuses = [result for result in results if isinstance(result, PayrollRunStatus)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert statuses == [PayrollRunStatus.FINALIZED]
    assert len(errors) == 1
    assert errors[0].__class__.__name__ == "PayrollRunNotCalculatedError"
