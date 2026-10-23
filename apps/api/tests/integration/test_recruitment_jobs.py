"""HTTP integration tests for recruitment jobs (T2).

Every test drives the real router, domain service and PostgreSQL adapter
through the shared session. The router clock is injected
(`recruitment_api._now`), so transition stamps never depend on wall time.

Covered here (`.ai/features/recruitment.md` → T2):

- create → `201 draft` + audit; open → `open` + `job.opened`; close → `closed`
  + `job.closed`, both in the outbox with identifiers-only payloads;
- the pinned failure codes at the HTTP edge (`403 recruitment_denied`,
  `409 job_not_draft`, `409 job_not_open`, `404 job_not_found`, `404`
  department/position/location refs);
- edit while `draft`/`open`, bounds (`openings < 1`, explicit nulls) as `422`;
- list filters (`status`, `q`, `department_id`) and clamped pagination;
- read visibility (management full, employee/finance denied, manager scoped);
- tenant isolation: B sees and touches nothing of A's.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import recruitment as recruitment_api
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.identity import Role
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    USER,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PINNED_JOB_KEYS = {
    "id",
    "tenant_id",
    "title",
    "description",
    "department_id",
    "position_id",
    "location_id",
    "openings",
    "status",
    "created_by",
    "opened_at",
    "closed_at",
    "created_at",
    "updated_at",
}


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


def _payload(refs: tuple[str, str, str], **overrides: object) -> dict[str, object]:
    department_id, position_id, location_id = refs
    payload: dict[str, object] = {
        "title": "Senior Engineer",
        "description": "Build the workforce platform",
        "department_id": department_id,
        "position_id": position_id,
        "location_id": location_id,
        "openings": 1,
    }
    payload.update(overrides)
    return payload


async def _create_job(
    client: AsyncClient,
    refs: tuple[str, str, str],
    **overrides: object,
) -> Response:
    return await client.post(
        "/api/v1/recruitment/jobs", json=_payload(refs, **overrides)
    )


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "job")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _hr() -> Any:
    return identity(role=Role.HR)


async def test_job_lifecycle_transitions_stamps_events_and_audit(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": CLOCK}
    monkeypatch.setattr(recruitment_api, "_now", lambda: clock["now"])

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        created = await _create_job(hr, refs, description="Build the thing")
        opened = await hr.post(f"/api/v1/recruitment/jobs/{created.json()['id']}/open")
        clock["now"] = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)
        closed = await hr.post(f"/api/v1/recruitment/jobs/{created.json()['id']}/close")

    assert created.status_code == 201
    draft = created.json()
    assert set(draft) == PINNED_JOB_KEYS
    assert draft["tenant_id"] == str(TENANT_A)
    assert draft["title"] == "Senior Engineer"
    assert draft["description"] == "Build the thing"
    assert draft["status"] == "draft"
    assert draft["created_by"] == str(USER)
    assert draft["openings"] == 1
    assert draft["opened_at"] is None
    assert draft["closed_at"] is None

    assert opened.status_code == 200
    opened_body = opened.json()
    assert opened_body["status"] == "open"
    assert opened_body["opened_at"] is not None
    assert opened_body["closed_at"] is None

    assert closed.status_code == 200
    closed_body = closed.json()
    assert closed_body["status"] == "closed"
    assert closed_body["opened_at"] == opened_body["opened_at"]
    assert closed_body["closed_at"] is not None

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "job.created",
        "job.opened",
        "job.closed",
    ]
    assert audit[0].changes is not None
    assert audit[0].changes["status"] == {"old": None, "new": "draft"}
    assert audit[1].changes == {"status": {"old": "draft", "new": "open"}}
    assert audit[2].changes == {"status": {"old": "open", "new": "closed"}}
    assert all(row.actor_id == USER for row in audit)

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == ["job.opened", "job.closed"]
    opening_payload = outbox[0].payload
    assert opening_payload["version"] == 1
    assert opening_payload["tenant_id"] == str(TENANT_A)
    assert opening_payload["actor_id"] == str(USER)
    assert opening_payload["data"] == {
        "job_id": draft["id"],
        "position_id": refs[1],
        "department_id": refs[0],
        "location_id": refs[2],
        "openings": 1,
    }
    assert outbox[1].payload["data"] == {
        "job_id": draft["id"],
        "closed_by": str(USER),
    }
    # The job's text never reaches the bus.
    assert "Build the thing" not in str(opening_payload)
    assert "Senior Engineer" not in str(opening_payload)


async def test_job_failures_and_denied_roles(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        employee_create = await _create_job(employee, refs)
    async with client_factory(identity(role=Role.MANAGER)) as manager:
        manager_create = await _create_job(manager, refs)
    async with client_factory(identity(role=Role.FINANCE)) as finance:
        finance_create = await _create_job(finance, refs)

    _assert_error(employee_create, 403, "recruitment_denied")
    _assert_error(manager_create, 403, "recruitment_denied")
    _assert_error(finance_create, 403, "recruitment_denied")

    async with client_factory(_hr()) as hr:
        created = await _create_job(hr, refs)
        job_id = created.json()["id"]
        opened = await hr.post(f"/api/v1/recruitment/jobs/{job_id}/open")
        opened_again = await hr.post(f"/api/v1/recruitment/jobs/{job_id}/open")

        second = await _create_job(hr, refs, title="Designer")
        close_draft = await hr.post(
            f"/api/v1/recruitment/jobs/{second.json()['id']}/close"
        )

        await hr.post(f"/api/v1/recruitment/jobs/{job_id}/close")
        update_closed = await hr.patch(
            f"/api/v1/recruitment/jobs/{job_id}", json={"title": "Nope"}
        )
        unknown = await hr.get(
            "/api/v1/recruitment/jobs/00000000-0000-4000-8000-0000000000ff"
        )

    assert opened.status_code == 200
    _assert_error(opened_again, 409, "job_not_draft")
    _assert_error(close_draft, 409, "job_not_open")
    _assert_error(update_closed, 409, "job_not_open")
    _assert_error(unknown, 404, "job_not_found")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        employee_get = await employee.get(f"/api/v1/recruitment/jobs/{job_id}")
        employee_list = await employee.get("/api/v1/recruitment/jobs")
    _assert_error(employee_get, 403, "recruitment_denied")
    _assert_error(employee_list, 403, "recruitment_denied")

    async with client_factory(identity(role=Role.MANAGER)) as manager:
        manager_get = await manager.get(f"/api/v1/recruitment/jobs/{job_id}")
        manager_list = await manager.get("/api/v1/recruitment/jobs")

    # A manager has no interview assignment, so existence is not leaked.
    _assert_error(manager_get, 404, "job_not_found")
    assert manager_list.status_code == 200
    assert manager_list.json()["items"] == []
    assert manager_list.json()["page"]["total_items"] == 0


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("department_id", "department_not_found"),
        ("position_id", "position_not_found"),
        ("location_id", "location_not_found"),
    ],
)
async def test_create_job_validates_org_refs(
    client_factory: ClientFactory,
    field: str,
    code: str,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        response = await _create_job(
            hr, refs, **{field: "00000000-0000-4000-8000-0000000000ff"}
        )

    _assert_error(response, 404, code)


async def test_create_job_rejects_unknown_fields_and_bounds(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        unknown = await hr.post(
            "/api/v1/recruitment/jobs",
            json={**_payload(refs), "status": "open"},
        )
        zero_openings = await _create_job(hr, refs, openings=0)
        blank_title = await _create_job(hr, refs, title="")

    _assert_error(unknown, 422, "validation_error")
    _assert_error(zero_openings, 422, "validation_error")
    _assert_error(blank_title, 422, "validation_error")


async def test_update_job_records_changes_and_rejects_explicit_nulls(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        created = await _create_job(hr, refs)
        job_id = created.json()["id"]
        updated = await hr.patch(
            f"/api/v1/recruitment/jobs/{job_id}",
            json={"title": "Staff Engineer", "description": None, "openings": 2},
        )
        null_title = await hr.patch(
            f"/api/v1/recruitment/jobs/{job_id}", json={"title": None}
        )

    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Staff Engineer"
    assert body["description"] is None
    assert body["openings"] == 2
    assert body["status"] == "draft"
    _assert_error(null_title, 422, "validation_error")

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["job.created", "job.updated"]
    assert audit[-1].changes == {
        "title": {"old": "Senior Engineer", "new": "Staff Engineer"},
        "description": {"old": "Build the workforce platform", "new": None},
        "openings": {"old": 1, "new": 2},
    }


async def test_list_jobs_filters_and_clamped_pagination(
    client_factory: ClientFactory,
) -> None:
    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        other_department = (
            await admin.post("/api/v1/departments", json={"name": "Design"})
        ).json()["id"]

    async with client_factory(_hr()) as hr:
        first = await _create_job(hr, refs, title="Backend Engineer")
        second = await _create_job(
            hr,
            refs,
            title="Product Designer",
            department_id=other_department,
        )
        third = await _create_job(hr, refs, title="Platform Engineer")
        await hr.post(f"/api/v1/recruitment/jobs/{third.json()['id']}/open")

        all_jobs = await hr.get("/api/v1/recruitment/jobs")
        open_jobs = await hr.get("/api/v1/recruitment/jobs", params={"status": "open"})
        search = await hr.get("/api/v1/recruitment/jobs", params={"q": "design"})
        by_department = await hr.get(
            "/api/v1/recruitment/jobs",
            params={"department_id": other_department},
        )
        clamped = await hr.get(
            "/api/v1/recruitment/jobs", params={"page": 0, "page_size": 1000}
        )
        paged = await hr.get(
            "/api/v1/recruitment/jobs", params={"page": 1, "page_size": 1}
        )
        invalid_status = await hr.get(
            "/api/v1/recruitment/jobs", params={"status": "archived"}
        )

    assert all_jobs.status_code == 200
    assert all_jobs.json()["page"]["total_items"] == 3
    assert [item["id"] for item in open_jobs.json()["items"]] == [third.json()["id"]]
    assert [item["id"] for item in search.json()["items"]] == [second.json()["id"]]
    assert [item["id"] for item in by_department.json()["items"]] == [
        second.json()["id"]
    ]
    assert clamped.json()["page"]["page"] == 1
    assert clamped.json()["page"]["page_size"] == 100
    assert paged.json()["page"]["total_pages"] == 3
    assert len(paged.json()["items"]) == 1
    _assert_error(invalid_status, 422, "validation_error")

    # Newest first is the pinned order.
    ids = [item["id"] for item in all_jobs.json()["items"]]
    assert set(ids) == {first.json()["id"], second.json()["id"], third.json()["id"]}


async def test_recruitment_jobs_are_tenant_isolated(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as tenant_a_admin:
        refs_a = await create_org_refs(tenant_a_admin)
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin, suffix=" B")

    async with client_factory(_hr()) as tenant_a_hr:
        created = await _create_job(tenant_a_hr, refs_a)
    job_id = created.json()["id"]

    async with client_factory(identity(tenant=TENANT_B, role=Role.HR)) as tenant_b_hr:
        detail = await tenant_b_hr.get(f"/api/v1/recruitment/jobs/{job_id}")
        update = await tenant_b_hr.patch(
            f"/api/v1/recruitment/jobs/{job_id}", json={"title": "Nope"}
        )
        open_job = await tenant_b_hr.post(f"/api/v1/recruitment/jobs/{job_id}/open")
        close_job = await tenant_b_hr.post(f"/api/v1/recruitment/jobs/{job_id}/close")
        listed = await tenant_b_hr.get("/api/v1/recruitment/jobs")
        # A tenant-A org ref is foreign to tenant B.
        foreign_ref = await _create_job(tenant_b_hr, refs_a)
        own = await _create_job(tenant_b_hr, refs_b, title="B job")

    _assert_error(detail, 404, "job_not_found")
    _assert_error(update, 404, "job_not_found")
    _assert_error(open_job, 404, "job_not_found")
    _assert_error(close_job, 404, "job_not_found")
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    _assert_error(foreign_ref, 404, "department_not_found")
    assert own.status_code == 201
    assert own.json()["tenant_id"] == str(TENANT_B)
