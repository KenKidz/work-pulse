"""T6 parity harness — fold projection vs CDC projection (AC 2–5).

One API-written scenario drives both projection paths from the **same** source
rows:

- the fold path replays the committed `event_outbox` payloads through the
  production `ReportsConsumer` into the canonical ClickHouse database;
- the CDC path wraps the captured `employees`/`employments`/
  `attendance_records`/`overtime_requests`/`leave_requests`/`leave_types`
  row state in Debezium-shaped snapshot envelopes and runs the production
  `CdcSinkConsumer` into the D2 shadow database.

The scenario includes a mid-period transfer, a backdated transfer applied after
its fact exists, the full overtime state machine (including an un-verified
claim and a payroll finalize to `included_in_payroll`), a late check-in/out
with `minutes_late`, approved and pending leave, and a second tenant. The four
report aggregates are read through the same `ClickhouseReportsRepository`
against both databases and must be exactly equal (AC 2), then pinned to their
expected per-business-date attribution on **both** paths (AC 3), checked for
tenant isolation (AC 4) and for the shadow readiness fence (AC 5).

Everything is driven to a commit boundary under bounded waits; there are no
fixed sleeps (`.ai/testing.md`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from clickhouse_connect.driver.client import Client
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import attendance as attendance_api
from app.api.v1 import leave as leave_api
from app.config import get_settings
from app.domains.overtime.models import OvertimeRequest
from app.domains.reports.exceptions import ReportsUnavailableError
from app.domains.reports.schemas import OvertimeAggregateRow, ReportQuery
from app.infrastructure.clickhouse.cdc_projection import ClickhouseCdcWriter
from app.infrastructure.clickhouse.client import (
    dispose_clickhouse,
    get_clickhouse_client,
)
from app.infrastructure.clickhouse.projection import ClickhouseProjectionWriter
from app.infrastructure.clickhouse.reports import ClickhouseReportsRepository
from app.infrastructure.clickhouse.schema import (
    SCHEMA_TABLES,
    apply_clickhouse_schema,
)
from app.shared.identity import Role
from tests.integration.cdc_parity_helpers import (
    captured_envelopes,
    outbox_envelopes,
    run_cdc_sink,
    run_fold_consumer,
)
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)
from workers.payroll.main import build_job_unit

RANGE_FROM = date(2026, 8, 1)
RANGE_TO = date(2026, 9, 30)
AUG = date(2026, 8, 1)
SEP = date(2026, 9, 1)
HIRED = date(2026, 1, 5)
MID_TRANSFER = date(2026, 9, 15)
BACKDATED_TRANSFER = date(2026, 9, 5)
ATTENDANCE_IN = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
ATTENDANCE_OUT = datetime(2026, 9, 18, 17, 30, tzinfo=UTC)
LEAVE_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
PAYROLL_PERIOD = "2026-09"
HR_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000f1")
HR_EMPLOYEE_B = UUID("00000000-0000-4000-8000-0000000000f2")


@dataclass(frozen=True, slots=True)
class Scenario:
    """The ids the attribution and isolation assertions refer to."""

    department_a: UUID
    department_b: UUID
    tenant_b_department: UUID


@pytest.fixture
def projections() -> Iterator[tuple[Client, Client, ClickhouseCdcWriter]]:
    """Fresh canonical + D2 shadow databases around one parity test."""
    canonical = get_clickhouse_client()
    _drop_tables(canonical)
    apply_clickhouse_schema(canonical)

    writer = ClickhouseCdcWriter.connect(get_settings().cdc_projection_database)
    assert get_settings().cdc_projection_database == "workpulse_test_cdc"
    _drop_tables(writer._client)
    writer.apply_schema()
    try:
        yield canonical, writer._client, writer
    finally:
        _drop_tables(writer._client)
        writer.close()
        _drop_tables(canonical)
        dispose_clickhouse()


def _drop_tables(client: Client) -> None:
    for table in SCHEMA_TABLES:
        client.command(f"DROP TABLE IF EXISTS {table}")


async def _create_employee(
    admin: AsyncClient,
    refs: tuple[str, str, str],
    *,
    first_name: str,
    last_name: str,
    email: str,
    hired_at: date = HIRED,
) -> dict[str, Any]:
    response = await admin.post(
        "/api/v1/employees",
        json=employee_payload(
            refs,
            first_name=first_name,
            last_name=last_name,
            email=email,
            hired_at=hired_at.isoformat(),
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _transfer(
    admin: AsyncClient,
    employee_id: str,
    department_id: str,
    effective_from: date,
) -> None:
    response = await admin.post(
        f"/api/v1/employees/{employee_id}/transfers",
        json={
            "department_id": department_id,
            "effective_from": effective_from.isoformat(),
        },
    )
    assert response.status_code == 200, response.text


async def _submit_overtime(employee: AsyncClient, work_date: date, hours: str) -> str:
    response = await employee.post(
        "/api/v1/overtime/requests",
        json={"work_date": work_date.isoformat(), "hours": hours},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _decide(hr: AsyncClient, request_id: str, action: str) -> None:
    response = await hr.post(
        f"/api/v1/overtime/requests/{request_id}/{action}", json={}
    )
    assert response.status_code == 200, response.text


async def _verified_claim(
    client_factory: ClientFactory,
    employee_identity: Any,
    hr_identity: Any,
    *,
    work_date: date,
    hours: str,
    reject: bool = False,
) -> str:
    async with client_factory(employee_identity) as employee:
        request_id = await _submit_overtime(employee, work_date, hours)
    async with client_factory(hr_identity) as hr:
        await _decide(hr, request_id, "approve")
        await _decide(hr, request_id, "verify")
        if reject:
            await _decide(hr, request_id, "reject")
    return request_id


async def _drive_scenario(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> Scenario:
    """Write the parity scenario through the real API services."""
    async with client_factory() as admin:
        refs_a = await create_org_refs(admin, suffix=" Parity A")
        refs_b = await create_org_refs(admin, suffix=" Parity B")
        employee_one = await _create_employee(
            admin,
            refs_a,
            first_name="Ada",
            last_name="Parity",
            email="ada.parity@example.test",
        )
        employee_two = await _create_employee(
            admin,
            refs_a,
            first_name="Bea",
            last_name="Parity",
            email="bea.parity@example.test",
        )

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b_tenant = await create_org_refs(tenant_b_admin, suffix=" Parity C")
        employee_b = await _create_employee(
            tenant_b_admin,
            refs_b_tenant,
            first_name="Tina",
            last_name="Tenant",
            email="tina.tenant@example.test",
            hired_at=date(2026, 1, 10),
        )

    employee_one_id = UUID(employee_one["id"])
    employee_two_id = UUID(employee_two["id"])
    employee_b_id = UUID(employee_b["id"])
    employee_one_identity = identity(role=Role.EMPLOYEE, employee_id=employee_one_id)
    employee_two_identity = identity(role=Role.EMPLOYEE, employee_id=employee_two_id)
    employee_b_identity = identity(
        tenant=TENANT_B, role=Role.EMPLOYEE, employee_id=employee_b_id
    )
    hr_identity = identity(role=Role.HR, employee_id=HR_EMPLOYEE)
    hr_identity_b = identity(tenant=TENANT_B, role=Role.HR, employee_id=HR_EMPLOYEE_B)

    async with client_factory() as admin:
        await _transfer(
            admin, employee_one["id"], refs_b[0], effective_from=MID_TRANSFER
        )

    # Employee one: a fact before the mid-period transfer and a verified claim
    # on each side of it. The September claims are flipped by payroll below.
    claim_august = await _verified_claim(
        client_factory,
        employee_one_identity,
        hr_identity,
        work_date=date(2026, 8, 20),
        hours="2.50",
    )
    claim_september = await _verified_claim(
        client_factory,
        employee_one_identity,
        hr_identity,
        work_date=date(2026, 9, 10),
        hours="3.00",
    )

    # Employee two's fact is projected under the old assignment, then a
    # backdated transfer moves the interval start before it: both paths must
    # re-attribute the existing fact to department B (AC 3).
    claim_backdated = await _verified_claim(
        client_factory,
        employee_two_identity,
        hr_identity,
        work_date=date(2026, 9, 10),
        hours="4.00",
    )
    async with client_factory() as admin:
        await _transfer(
            admin,
            employee_two["id"],
            refs_b[0],
            effective_from=BACKDATED_TRANSFER,
        )

    # A verified-then-rejected claim: the fold un-verifies it and the CDC row
    # maps to `rejected`; neither path counts the hours.
    claim_rejected = await _verified_claim(
        client_factory,
        employee_one_identity,
        hr_identity,
        work_date=date(2026, 9, 20),
        hours="1.25",
        reject=True,
    )

    async with client_factory() as admin:
        shift = await admin.post(
            "/api/v1/shifts",
            json={
                "name": "Parity shift",
                "start_time": "09:00",
                "end_time": "18:00",
                "grace_minutes": 0,
            },
        )
        assert shift.status_code == 201, shift.text

    monkeypatch.setattr(attendance_api, "_now", lambda: ATTENDANCE_IN)
    async with client_factory(employee_one_identity) as employee:
        check_in = await employee.post(
            "/api/v1/attendance/check-ins", json={"work_mode": "onsite"}
        )
        assert check_in.status_code == 201, check_in.text
        attendance_id = check_in.json()["id"]

    monkeypatch.setattr(attendance_api, "_now", lambda: ATTENDANCE_OUT)
    async with client_factory(employee_one_identity) as employee:
        check_out = await employee.post(
            f"/api/v1/attendance/records/{attendance_id}/check-out", json={}
        )
        assert check_out.status_code == 200, check_out.text

    monkeypatch.setattr(leave_api, "_now", lambda: LEAVE_NOW)
    async with client_factory(employee_one_identity) as employee:
        types = (await employee.get("/api/v1/leave/types")).json()
        annual = next(row for row in types if row["code"] == "annual")
        approved = await employee.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": annual["id"],
                "start_date": "2026-09-21",
                "end_date": "2026-09-22",
            },
        )
        assert approved.status_code == 201, approved.text
        pending = await employee.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": annual["id"],
                "start_date": "2026-09-24",
                "end_date": "2026-09-24",
            },
        )
        assert pending.status_code == 201, pending.text
    async with client_factory(hr_identity) as hr:
        decided = await hr.post(
            f"/api/v1/leave/requests/{approved.json()['id']}/approve", json={}
        )
        assert decided.status_code == 200, decided.text

    # Tenant B's own verified fact: present in both projections, never visible
    # to a tenant-A report (AC 4).
    await _verified_claim(
        client_factory,
        employee_b_identity,
        hr_identity_b,
        work_date=date(2026, 9, 12),
        hours="5.00",
    )

    # A payroll finalize flips both September claims to `included_in_payroll`
    # in OLTP. The fold never sees an event for that flip (it stays
    # `hr_verified`); the CDC sink maps it back to `hr_verified` (D6).
    async with client_factory() as admin:
        for employee_id in (employee_one_id, employee_two_id):
            salary = await admin.post(
                "/api/v1/payroll/salaries",
                json={
                    "employee_id": str(employee_id),
                    "amount": "1000.00",
                    "effective_from": "2026-01-01",
                },
            )
            assert salary.status_code == 201, salary.text
        run = await admin.post("/api/v1/payroll/runs", json={"period": PAYROLL_PERIOD})
        assert run.status_code == 201, run.text
        run_id = UUID(run.json()["id"])

    calculated = await build_job_unit(session).calculate(run_id, datetime.now(UTC))
    assert calculated.status.value == "calculated"

    async with client_factory() as admin:
        finalized = await admin.post(f"/api/v1/payroll/runs/{run_id}/finalize")
        assert finalized.status_code == 200, finalized.text
    await session.flush()

    # Prove the scenario really exercised the D6 path: payroll flipped the two
    # September claims (no event exists for it) and the un-verified claim is
    # terminally rejected, so only the fold's/CDC's `hr_verified` rows count.
    statuses = {
        claim_id: await session.scalar(
            select(OvertimeRequest.status).where(OvertimeRequest.id == UUID(claim_id))
        )
        for claim_id in (
            claim_august,
            claim_september,
            claim_backdated,
            claim_rejected,
        )
    }
    assert statuses[claim_september] == "included_in_payroll"
    assert statuses[claim_backdated] == "included_in_payroll"
    assert statuses[claim_august] == "hr_verified"
    assert statuses[claim_rejected] == "rejected"

    return Scenario(
        department_a=UUID(refs_a[0]),
        department_b=UUID(refs_b[0]),
        tenant_b_department=UUID(refs_b_tenant[0]),
    )


def _query(
    tenant_id: UUID,
    *,
    group_by: tuple[str, ...] = ("department",),
    interval: str = "month",
) -> ReportQuery:
    return ReportQuery(
        tenant_id=tenant_id,
        start=RANGE_FROM,
        end=RANGE_TO,
        interval=interval,
        group_by=group_by,
    )


def _parity_cases() -> tuple[tuple[str, ReportQuery], ...]:
    return (
        ("overtime_aggregates", _query(TENANT_A)),
        ("overtime_aggregates", _query(TENANT_A, interval="total")),
        ("attendance_aggregates", _query(TENANT_A)),
        ("leave_aggregates", _query(TENANT_A, group_by=("leave_type",))),
        ("headcount_aggregates", _query(TENANT_A)),
        ("headcount_aggregates", _query(TENANT_A, interval="total")),
        ("overtime_aggregates", _query(TENANT_B)),
        ("headcount_aggregates", _query(TENANT_B)),
    )


def _assert_parity(canonical: Client, shadow: Client) -> None:
    """The exact same repository answers both projections (AC 2)."""
    for method, query in _parity_cases():
        canonical_rows = list(
            getattr(ClickhouseReportsRepository(canonical), method)(query)
        )
        shadow_rows = list(getattr(ClickhouseReportsRepository(shadow), method)(query))
        assert shadow_rows == canonical_rows, (
            method,
            query,
            canonical_rows,
            shadow_rows,
        )


def _assert_expected_attribution(
    repository: ClickhouseReportsRepository, scenario: Scenario
) -> None:
    """Every fact groups by the assignment in effect on its business date.

    Runs against both clients: the fold's stamped facts and the CDC's
    stamp-free facts must both resolve through the query-time as-of join (D3).
    """
    dept_a = str(scenario.department_a)
    dept_b = str(scenario.department_b)

    overtime = {
        (row.bucket, row.keys[0]): row
        for row in repository.overtime_aggregates(_query(TENANT_A))
    }
    assert set(overtime) == {(AUG, dept_a), (SEP, dept_a), (SEP, dept_b)}
    assert overtime[(AUG, dept_a)].verified_hours == Decimal("2.50")
    assert overtime[(SEP, dept_a)].verified_hours == Decimal("3.00")
    # The backdated transfer moved employee two's fact from A to B.
    assert overtime[(SEP, dept_b)].verified_hours == Decimal("4.00")
    assert sum(
        (row.verified_hours for row in overtime.values()), Decimal("0")
    ) == Decimal("9.50")
    for row in overtime.values():
        assert row.verified_request_count == 1
        assert row.verified_day_count == 1
        assert row.active_employee_count == 2

    attendance = {
        (row.bucket, row.keys[0]): row
        for row in repository.attendance_aggregates(_query(TENANT_A))
    }
    assert set(attendance) == {(SEP, dept_b)}
    attendance_row = attendance[(SEP, dept_b)]
    assert attendance_row.checkin_count == 1
    assert attendance_row.worked_day_count == 1
    assert attendance_row.worked_minutes == 480
    assert attendance_row.late_count == 1
    assert attendance_row.minutes_late_total == 30

    leave = {
        (row.bucket, row.keys[0]): row
        for row in repository.leave_aggregates(
            _query(TENANT_A, group_by=("leave_type",))
        )
    }
    assert set(leave) == {(SEP, "annual")}
    assert leave[(SEP, "annual")].approved_day_count == 2
    assert leave[(SEP, "annual")].approved_request_count == 1

    headcount = {
        (row.bucket, row.keys[0]): row
        for row in repository.headcount_aggregates(_query(TENANT_A))
    }
    assert set(headcount) == {(AUG, dept_a), (SEP, dept_b)}
    assert headcount[(AUG, dept_a)].headcount == 2
    assert headcount[(AUG, dept_a)].joiners == 0
    assert headcount[(AUG, dept_a)].leavers == 0
    assert headcount[(SEP, dept_b)].headcount == 2
    assert headcount[(SEP, dept_b)].joiners == 0
    assert headcount[(SEP, dept_b)].leavers == 0

    [total] = repository.headcount_aggregates(_query(TENANT_A, interval="total"))
    assert total.bucket is None
    assert total.keys == (dept_b,)
    assert total.headcount == 2


def _assert_tenant_isolation(
    canonical: Client, shadow: Client, scenario: Scenario
) -> None:
    """Tenant B rows exist in both projections but never leak into A (AC 4)."""
    tenant_b_department = str(scenario.tenant_b_department)
    for client in (canonical, shadow):
        repository = ClickhouseReportsRepository(client)

        tenant_a = list(repository.overtime_aggregates(_query(TENANT_A)))
        assert all(row.keys[0] != tenant_b_department for row in tenant_a)
        assert sum((row.verified_hours for row in tenant_a), Decimal("0")) == Decimal(
            "9.50"
        )
        assert all(
            row.keys[0] in {str(scenario.department_a), str(scenario.department_b)}
            for row in tenant_a
        )

        assert list(repository.overtime_aggregates(_query(TENANT_B))) == [
            OvertimeAggregateRow(
                bucket=SEP,
                keys=(tenant_b_department,),
                verified_hours=Decimal("5.00"),
                verified_request_count=1,
                verified_day_count=1,
                active_employee_count=1,
            )
        ]

        tenant_a_headcount = {
            (row.bucket, row.keys[0])
            for row in repository.headcount_aggregates(_query(TENANT_A))
        }
        assert all(key != tenant_b_department for _, key in tenant_a_headcount)
        tenant_b_headcount = {
            (row.bucket, row.keys[0])
            for row in repository.headcount_aggregates(_query(TENANT_B))
        }
        assert tenant_b_headcount == {
            (AUG, tenant_b_department),
            (SEP, tenant_b_department),
        }


def _assert_shadow_ready_gate(shadow: Client) -> None:
    """A fenced CDC projection refuses report reads (AC 5)."""
    shadow.insert(
        "projection_state",
        [["reports", 0, datetime.now(UTC) + timedelta(hours=1)]],
        column_names=["key", "ready", "updated_at"],
    )
    with pytest.raises(ReportsUnavailableError):
        ClickhouseReportsRepository(shadow).ensure_ready()
    with pytest.raises(ReportsUnavailableError):
        ClickhouseReportsRepository(shadow).overtime_aggregates(_query(TENANT_A))


async def test_cdc_projection_matches_the_fold_and_attributes_by_business_date(
    client_factory: ClientFactory,
    session: AsyncSession,
    projections: tuple[Client, Client, ClickhouseCdcWriter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical, shadow, cdc_writer = projections
    scenario = await _drive_scenario(client_factory, session, monkeypatch)

    await run_fold_consumer(
        await outbox_envelopes(session), ClickhouseProjectionWriter(canonical)
    )
    await run_cdc_sink(await captured_envelopes(session), cdc_writer)
    readiness = cdc_writer.readiness()
    assert readiness is not None and readiness[0] == 1

    _assert_parity(canonical, shadow)
    for client in (canonical, shadow):
        _assert_expected_attribution(ClickhouseReportsRepository(client), scenario)
    _assert_tenant_isolation(canonical, shadow, scenario)
    _assert_shadow_ready_gate(shadow)


async def test_shadow_readiness_fence_answers_the_api_503(
    client_factory: ClientFactory,
    projections: tuple[Client, Client, ClickhouseCdcWriter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing 503 contract holds when the CDC projection is fenced (AC 5)."""
    _, shadow, _ = projections
    shadow.insert(
        "projection_state",
        [["reports", 0, datetime.now(UTC)]],
        column_names=["key", "ready", "updated_at"],
    )

    monkeypatch.setattr("app.dependencies.get_clickhouse_client", lambda: shadow)
    async with client_factory() as client:
        response = await client.get(
            "/api/v1/reports/overtime",
            params={"from": RANGE_FROM.isoformat(), "to": RANGE_TO.isoformat()},
        )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "reports_unavailable"
