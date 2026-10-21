"""HTTP integration tests for payroll config and deduction lines (T5).

Drives the real router → service → Postgres adapter against the test database
(:5433). Covers the pinned T5 surface:

- config defaults (USD / 160 / 1.50) and partial updates, audited;
- deduction-line CRUD-lite with kind-dependent `422`s, unique codes (`409`)
  and deterministic code ordering;
- the role matrix: Finance/Admin/Super Admin mutate, HR reads, employee and
  manager get `404` on reads (no existence leak) and `403` on writes;
- tenant isolation (tenant B sees none of tenant A's config/rules);
- audit entries carry no deduction values (amount-like rule values are PII).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.infrastructure.postgres.repositories.payroll import PostgresPayrollRepository
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    TENANT_B,
    ClientFactory,
    create_org_refs,
    identity,
)


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def _seed_actor(
    session: AsyncSession, refs: tuple[str, str, str], *, tenant: UUID = TENANT_A
) -> None:
    await PostgresEmployeeRepository(session).add(
        tenant,
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


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.action == action)
        .order_by(AuditEntry.occurred_at, AuditEntry.id)
    )
    return list(result.scalars().all())


# --- configuration -----------------------------------------------------------


async def test_config_is_provisioned_with_the_pinned_defaults(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        response = await client.get("/api/v1/payroll/config")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == str(TENANT_A)
    assert body["currency"] == "USD"
    assert body["standard_monthly_hours"] == 160.0
    assert body["overtime_multiplier"] == 1.5


async def test_config_partial_update_is_audited(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        updated = await client.put(
            "/api/v1/payroll/config",
            json={
                "currency": "EUR",
                "standard_monthly_hours": 150,
                "overtime_multiplier": 1.25,
            },
        )
        assert updated.status_code == 200
        fetched = await client.get("/api/v1/payroll/config")

    assert fetched.json()["currency"] == "EUR"
    assert fetched.json()["standard_monthly_hours"] == 150.0
    assert fetched.json()["overtime_multiplier"] == 1.25
    assert fetched.json()["updated_at"] is not None

    entries = await _audit_rows(session, "payroll.config_updated")
    assert len(entries) == 1
    assert entries[0].entity_type == "payroll_config"
    changes = entries[0].changes
    assert changes is not None
    assert changes["currency"] == {"old": "USD", "new": "EUR"}
    assert set(changes) == {
        "currency",
        "standard_monthly_hours",
        "overtime_multiplier",
    }


async def test_config_validation_rejects_zero_hours_and_lowercase_currency(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        zero = await client.put(
            "/api/v1/payroll/config", json={"standard_monthly_hours": 0}
        )
        currency = await client.put("/api/v1/payroll/config", json={"currency": "usd"})

    _assert_error(zero, 422, "validation_error")
    _assert_error(currency, 422, "validation_error")


async def test_config_role_matrix(client_factory: ClientFactory) -> None:
    async with client_factory(identity(role=Role.HR)) as hr:
        assert (await hr.get("/api/v1/payroll/config")).status_code == 200
        _assert_error(
            await hr.put("/api/v1/payroll/config", json={"currency": "EUR"}),
            403,
            "forbidden",
        )

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        _assert_error(
            await employee.get("/api/v1/payroll/config"), 404, "payroll_not_found"
        )
        _assert_error(
            await employee.put("/api/v1/payroll/config", json={"currency": "EUR"}),
            403,
            "forbidden",
        )

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        _assert_error(
            await manager.get("/api/v1/payroll/config"), 404, "payroll_not_found"
        )


async def test_config_is_tenant_scoped(client_factory: ClientFactory) -> None:
    async with client_factory() as tenant_a:
        await tenant_a.put("/api/v1/payroll/config", json={"currency": "EUR"})

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        response = await tenant_b.get("/api/v1/payroll/config")

    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(TENANT_B)
    assert response.json()["currency"] == "USD"


async def test_provisioning_never_overwrites_a_saved_config(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    """The first-read/default path is insert-if-absent, not an upsert race."""
    async with client_factory() as client:
        saved = await client.put(
            "/api/v1/payroll/config",
            json={
                "currency": "EUR",
                "standard_monthly_hours": 140,
                "overtime_multiplier": 1.75,
            },
        )
        assert saved.status_code == 200

    config = await PostgresPayrollRepository(session).create_config_if_absent(
        TENANT_A,
        currency="USD",
        standard_monthly_hours=Decimal("160.00"),
        overtime_multiplier=Decimal("1.50"),
        now=datetime(2026, 10, 21, tzinfo=UTC),
    )

    assert config.currency == "EUR"
    assert config.standard_monthly_hours == Decimal("140.00")
    assert config.overtime_multiplier == Decimal("1.75")


async def test_partial_config_updates_only_touch_supplied_fields(
    session: AsyncSession,
) -> None:
    """Two partial updates to different fields both survive (no stale merge)."""
    repo = PostgresPayrollRepository(session)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    await repo.create_config_if_absent(
        TENANT_A,
        currency="USD",
        standard_monthly_hours=Decimal("160.00"),
        overtime_multiplier=Decimal("1.50"),
        now=now,
    )

    await repo.update_config_fields(TENANT_A, currency="EUR", now=now)
    updated = await repo.update_config_fields(
        TENANT_A, standard_monthly_hours=Decimal("150.00"), now=now
    )

    assert updated is not None
    assert updated.currency == "EUR"
    assert updated.standard_monthly_hours == Decimal("150.00")
    assert updated.overtime_multiplier == Decimal("1.50")


# --- deduction lines ---------------------------------------------------------


async def test_deduction_create_list_and_update_round_trip(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as client:
        created = await client.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "PENSION",
                "name": "Pension",
                "kind": "percent_of_base",
                "value": 5.5,
            },
        )
        deducted = created.json()
        listed = await client.get("/api/v1/payroll/deductions")
        patched = await client.patch(
            f"/api/v1/payroll/deductions/{deducted['id']}",
            json={"name": "Pension plan", "value": 6.25, "active": False},
        )

    assert created.status_code == 201
    assert deducted["code"] == "PENSION"
    assert deducted["kind"] == "percent_of_base"
    assert deducted["value"] == 5.5
    assert deducted["active"] is True

    assert listed.status_code == 200
    assert [row["code"] for row in listed.json()] == ["PENSION"]

    assert patched.status_code == 200
    assert patched.json()["name"] == "Pension plan"
    assert patched.json()["value"] == 6.25
    assert patched.json()["active"] is False

    entries = await _audit_rows(session, "payroll.deduction_created")
    assert len(entries) == 1
    changes = entries[0].changes
    assert changes is not None
    assert changes["value"] == "changed"
    assert "5.5" not in str(changes)
    assert (await _audit_rows(session, "payroll.deduction_updated"))[0].changes == {
        "name": {"old": "Pension", "new": "Pension plan"},
        "value": "changed",
        "active": {"old": True, "new": False},
    }


async def test_deduction_lines_are_ordered_by_code(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        for code in ("ZTAX", "ATAX", "MTAX"):
            created = await client.post(
                "/api/v1/payroll/deductions",
                json={
                    "code": code,
                    "name": code.title(),
                    "kind": "fixed_amount",
                    "value": 1.0,
                },
            )
            assert created.status_code == 201
        listed = await client.get("/api/v1/payroll/deductions")

    assert [row["code"] for row in listed.json()] == ["ATAX", "MTAX", "ZTAX"]


async def test_deduction_kind_specific_values_are_validated(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        percent_low = await client.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "P0",
                "name": "Percent low",
                "kind": "percent_of_base",
                "value": 0,
            },
        )
        percent_high = await client.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "P1",
                "name": "Percent high",
                "kind": "percent_of_base",
                "value": 100.01,
            },
        )
        fixed_low = await client.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "F0",
                "name": "Fixed low",
                "kind": "fixed_amount",
                "value": 0,
            },
        )

    _assert_error(percent_low, 422, "validation_error")
    _assert_error(percent_high, 422, "validation_error")
    _assert_error(fixed_low, 422, "validation_error")


async def test_partial_update_is_validated_against_the_stored_half(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        created = await client.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "LOAN",
                "name": "Loan",
                "kind": "fixed_amount",
                "value": 500,
            },
        )
        invalid = await client.patch(
            f"/api/v1/payroll/deductions/{created.json()['id']}",
            json={"kind": "percent_of_base"},
        )
        valid = await client.patch(
            f"/api/v1/payroll/deductions/{created.json()['id']}",
            json={"kind": "percent_of_base", "value": 10},
        )

    _assert_error(invalid, 422, "validation_error")
    assert valid.status_code == 200
    assert valid.json()["kind"] == "percent_of_base"
    assert valid.json()["value"] == 10.0


async def test_duplicate_deduction_code_is_a_conflict(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as client:
        payload = {
            "code": "TAX",
            "name": "Tax",
            "kind": "fixed_amount",
            "value": 10,
        }
        first = await client.post("/api/v1/payroll/deductions", json=payload)
        assert first.status_code == 201
        duplicate = await client.post("/api/v1/payroll/deductions", json=payload)

    _assert_error(duplicate, 409, "payroll_deduction_code_taken")


async def test_deduction_role_matrix_and_tenant_isolation(
    client_factory: ClientFactory, session: AsyncSession
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        created = await admin.post(
            "/api/v1/payroll/deductions",
            json={
                "code": "TAX",
                "name": "Tax",
                "kind": "fixed_amount",
                "value": 10,
            },
        )
        deduction_id = created.json()["id"]
    await _seed_actor(session, refs)

    async with client_factory(identity(role=Role.HR)) as hr:
        assert (await hr.get("/api/v1/payroll/deductions")).status_code == 200
        _assert_error(
            await hr.post(
                "/api/v1/payroll/deductions",
                json={
                    "code": "HRTAX",
                    "name": "HR tax",
                    "kind": "fixed_amount",
                    "value": 10,
                },
            ),
            403,
            "forbidden",
        )

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        _assert_error(
            await employee.get("/api/v1/payroll/deductions"),
            404,
            "payroll_not_found",
        )
        _assert_error(
            await employee.post(
                "/api/v1/payroll/deductions",
                json={
                    "code": "EMP",
                    "name": "Emp",
                    "kind": "fixed_amount",
                    "value": 10,
                },
            ),
            403,
            "forbidden",
        )

    async with client_factory(identity(tenant=TENANT_B)) as tenant_b:
        listed = await tenant_b.get("/api/v1/payroll/deductions")
        patched = await tenant_b.patch(
            f"/api/v1/payroll/deductions/{deduction_id}", json={"active": False}
        )

    assert listed.status_code == 200
    assert listed.json() == []
    _assert_error(patched, 404, "payroll_deduction_not_found")
