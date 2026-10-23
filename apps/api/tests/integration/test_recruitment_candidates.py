"""HTTP integration tests for recruitment candidates (T3).

Every test drives the real router, domain service and PostgreSQL adapter
through the shared session. The router clock is injected
(`recruitment_api._now`), so stamps never depend on wall time.

Covered here (`.ai/features/recruitment.md` → T3):

- create on an open job → `201 applied` + audit; `advance` twice →
  `interviewing`, `offered` with `candidate.created`/`candidate.advanced`
  outbox rows (identifiers and state only);
- pinned failures: duplicate application, `job_not_open`, `job_not_found`,
  `candidate_stage_conflict`, `candidate_not_open`, `candidate_not_found`,
  denied roles (`403 recruitment_denied`);
- contact edits with PII audit markers and the job-scoped email guard;
- list filters (`job_id`, `status`, `q`), clamped pagination and foreign filter
  ids `404`;
- interview-derived manager visibility (`CandidateDetail.interviews` is empty
  until T5; the scope join is proven with a directly seeded interview row);
- tenant isolation: B sees and touches nothing of A's.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.api.v1 import recruitment as recruitment_api
from app.domains.recruitment import models as recruitment_models
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.shared.identity import Role
from tests.integration.conftest import (
    TENANT_A,
    TENANT_B,
    USER,
    ClientFactory,
    create_org_refs,
    employee_payload,
    identity,
)

CLOCK = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PINNED_CANDIDATE_KEYS = {
    "id",
    "tenant_id",
    "job_id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "source",
    "notes",
    "status",
    "stage_note",
    "rejected_from",
    "hired_at",
    "employee_id",
    "created_at",
    "updated_at",
}


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


def _job_payload(refs: tuple[str, str, str], **overrides: object) -> dict[str, object]:
    department_id, position_id, location_id = refs
    payload: dict[str, object] = {
        "title": "Senior Engineer",
        "department_id": department_id,
        "position_id": position_id,
        "location_id": location_id,
        "openings": 1,
    }
    payload.update(overrides)
    return payload


def _candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.test",
        "phone": "+44 20 7946 0000",
        "source": "referral",
        "notes": "Referred by Grace",
    }
    payload.update(overrides)
    return payload


async def _create_open_job(
    client: AsyncClient, refs: tuple[str, str, str], **overrides: object
) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/recruitment/jobs", json=_job_payload(refs, **overrides)
    )
    assert created.status_code == 201
    job = created.json()
    opened = await client.post(f"/api/v1/recruitment/jobs/{job['id']}/open")
    assert opened.status_code == 200
    return job


async def _create_candidate(
    client: AsyncClient, job_id: str, **overrides: object
) -> Response:
    return await client.post(
        f"/api/v1/recruitment/jobs/{job_id}/candidates",
        json=_candidate_payload(**overrides),
    )


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "candidate")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _hr() -> Any:
    return identity(role=Role.HR)


async def test_candidate_lifecycle_events_audit_and_detail(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        created = await _create_candidate(hr, job["id"])
        candidate_id = created.json()["id"]
        interviewing = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/advance"
        )
        offered = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/advance"
        )
        detail = await hr.get(f"/api/v1/recruitment/candidates/{candidate_id}")

    assert created.status_code == 201
    candidate = created.json()
    assert set(candidate) == PINNED_CANDIDATE_KEYS
    assert candidate["tenant_id"] == str(TENANT_A)
    assert candidate["job_id"] == job["id"]
    assert candidate["status"] == "applied"
    assert candidate["source"] == "referral"
    assert candidate["phone"] == "+44 20 7946 0000"
    assert candidate["notes"] == "Referred by Grace"
    assert candidate["stage_note"] is None
    assert candidate["rejected_from"] is None
    assert candidate["hired_at"] is None
    assert candidate["employee_id"] is None

    assert interviewing.status_code == 200
    assert interviewing.json()["status"] == "interviewing"
    assert offered.status_code == 200
    assert offered.json()["status"] == "offered"

    assert detail.status_code == 200
    assert set(detail.json()) == PINNED_CANDIDATE_KEYS | {"interviews"}
    assert detail.json()["interviews"] == []

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "candidate.created",
        "candidate.advanced",
        "candidate.advanced",
    ]
    assert all(row.actor_id == USER for row in audit)
    assert audit[0].changes is not None
    assert audit[0].changes["status"] == {"old": None, "new": "applied"}
    assert audit[1].changes == {"status": {"old": "applied", "new": "interviewing"}}
    assert audit[2].changes == {"status": {"old": "interviewing", "new": "offered"}}

    outbox = [
        row
        for row in await _outbox_rows(session)
        if row.payload.get("data", {}).get("candidate_id") == candidate_id
    ]
    assert [row.event_type for row in outbox] == [
        "candidate.created",
        "candidate.advanced",
        "candidate.advanced",
    ]
    assert outbox[0].payload["data"] == {
        "candidate_id": candidate_id,
        "job_id": job["id"],
        "source": "referral",
    }
    assert outbox[1].payload["data"] == {
        "candidate_id": candidate_id,
        "job_id": job["id"],
        "from": "applied",
        "to": "interviewing",
    }
    assert outbox[2].payload["data"] == {
        "candidate_id": candidate_id,
        "job_id": job["id"],
        "from": "interviewing",
        "to": "offered",
    }

    # PII never reaches the bus or the audit values.
    rendered = " ".join(str(row.payload) for row in outbox)
    rendered += " ".join(str(row.changes) for row in audit)
    for pii in ("Ada", "Lovelace", "ada@example.test", "7946", "Grace"):
        assert pii not in rendered


async def test_candidate_failures_and_denied_roles(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        first = await _create_candidate(hr, job["id"])
        candidate_id = first.json()["id"]

        await hr.post(f"/api/v1/recruitment/candidates/{candidate_id}/advance")
        await hr.post(f"/api/v1/recruitment/candidates/{candidate_id}/advance")
        advance_offered = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/advance"
        )
        rejected = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/reject",
            json={"note": "Not a fit"},
        )
        reject_again = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/reject", json={}
        )
        withdraw_rejected = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/withdraw", json={}
        )

        await hr.post(f"/api/v1/recruitment/jobs/{job['id']}/close")
        create_on_closed = await _create_candidate(
            hr, job["id"], email="katherine@example.test"
        )
        create_on_unknown = await _create_candidate(
            hr, "00000000-0000-4000-8000-0000000000ff"
        )

        missing = "00000000-0000-4000-8000-0000000000ff"
        get_missing = await hr.get(f"/api/v1/recruitment/candidates/{missing}")
        update_missing = await hr.patch(
            f"/api/v1/recruitment/candidates/{missing}", json={"first_name": "X"}
        )
        advance_missing = await hr.post(
            f"/api/v1/recruitment/candidates/{missing}/advance"
        )

    assert first.status_code == 201
    _assert_error(advance_offered, 409, "candidate_stage_conflict")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejected_from"] == "offered"
    assert rejected.json()["stage_note"] == "Not a fit"
    _assert_error(reject_again, 409, "candidate_not_open")
    _assert_error(withdraw_rejected, 409, "candidate_not_open")
    _assert_error(create_on_closed, 409, "job_not_open")
    _assert_error(create_on_unknown, 404, "job_not_found")
    _assert_error(get_missing, 404, "candidate_not_found")
    _assert_error(update_missing, 404, "candidate_not_found")
    _assert_error(advance_missing, 404, "candidate_not_found")

    # Role gates fail before any repository call, so they survive the poisoned
    # session above (the same isolation trick the employees suites use).
    for role in (Role.EMPLOYEE, Role.MANAGER, Role.FINANCE):
        async with client_factory(identity(role=role)) as client:
            denied_create = await client.post(
                f"/api/v1/recruitment/jobs/{job['id']}/candidates",
                json=_candidate_payload(email="denied@example.test"),
            )
            denied_update = await client.patch(
                f"/api/v1/recruitment/candidates/{candidate_id}",
                json={"first_name": "X"},
            )
            denied_advance = await client.post(
                f"/api/v1/recruitment/candidates/{candidate_id}/advance"
            )
            denied_reject = await client.post(
                f"/api/v1/recruitment/candidates/{candidate_id}/reject", json={}
            )
            denied_withdraw = await client.post(
                f"/api/v1/recruitment/candidates/{candidate_id}/withdraw", json={}
            )

        _assert_error(denied_create, 403, "recruitment_denied")
        _assert_error(denied_update, 403, "recruitment_denied")
        _assert_error(denied_advance, 403, "recruitment_denied")
        _assert_error(denied_reject, 403, "recruitment_denied")
        _assert_error(denied_withdraw, 403, "recruitment_denied")


async def test_duplicate_application_is_refused(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        first = await _create_candidate(hr, job["id"])
        # The unique violation poisons the shared test session: keep it last.
        duplicate = await _create_candidate(hr, job["id"])

    assert first.status_code == 201
    _assert_error(duplicate, 409, "candidate_already_applied")
    assert "ada@example.test" not in duplicate.text  # never echo field values


async def test_candidate_update_audits_pii_fields(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        first = (await _create_candidate(hr, job["id"])).json()
        updated = await hr.patch(
            f"/api/v1/recruitment/candidates/{first['id']}",
            json={
                "first_name": "Augusta",
                "email": "ada.lovelace@example.test",
                "phone": None,
                "notes": "Second note",
                "source": "agency",
            },
        )
        explicit_null = await hr.patch(
            f"/api/v1/recruitment/candidates/{first['id']}",
            json={"first_name": None},
        )
        unknown_field = await hr.patch(
            f"/api/v1/recruitment/candidates/{first['id']}",
            json={"status": "hired"},
        )
        no_changes = await hr.patch(
            f"/api/v1/recruitment/candidates/{first['id']}", json={}
        )

    assert updated.status_code == 200
    body = updated.json()
    assert body["first_name"] == "Augusta"
    assert body["email"] == "ada.lovelace@example.test"
    assert body["phone"] is None
    assert body["notes"] == "Second note"
    assert body["source"] == "agency"
    _assert_error(explicit_null, 422, "validation_error")
    _assert_error(unknown_field, 422, "validation_error")
    assert no_changes.status_code == 200

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == ["candidate.created", "candidate.updated"]
    changes = audit[-1].changes or {}
    assert changes["first_name"] == "changed"
    assert changes["email"] == "changed"
    assert changes["phone"] == "changed"
    assert changes["notes"] == "changed"
    assert changes["source"] == {"old": "referral", "new": "agency"}
    assert "Augusta" not in str(changes)
    assert "Second note" not in str(changes)


async def test_candidate_update_email_guard_is_job_scoped(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        first = (await _create_candidate(hr, job["id"])).json()
        second = (
            await _create_candidate(hr, job["id"], email="grace@example.test")
        ).json()
        # The unique violation poisons the shared test session: keep it last.
        duplicate_email = await hr.patch(
            f"/api/v1/recruitment/candidates/{second['id']}",
            json={"email": first["email"]},
        )

    _assert_error(duplicate_email, 409, "candidate_already_applied")


async def test_list_candidates_filters_pagination_and_foreign_ids(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin, suffix=" B")

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs, title="Engineer")
        other_job = await _create_open_job(hr, refs, title="Designer")
        first = (
            await _create_candidate(
                hr, job["id"], first_name="Ada", last_name="Lovelace"
            )
        ).json()
        second = (
            await _create_candidate(
                hr,
                job["id"],
                first_name="Grace",
                last_name="Hopper",
                email="grace@example.test",
            )
        ).json()
        third = (
            await _create_candidate(
                hr,
                other_job["id"],
                first_name="Katherine",
                last_name="Johnson",
                email="kj@example.test",
            )
        ).json()
        await hr.post(f"/api/v1/recruitment/candidates/{second['id']}/advance")

        everything = await hr.get("/api/v1/recruitment/candidates")
        by_job = await hr.get(
            "/api/v1/recruitment/candidates", params={"job_id": job["id"]}
        )
        by_status = await hr.get(
            "/api/v1/recruitment/candidates", params={"status": "interviewing"}
        )
        by_first = await hr.get("/api/v1/recruitment/candidates", params={"q": "GRACE"})
        by_last = await hr.get(
            "/api/v1/recruitment/candidates", params={"q": "lovelace"}
        )
        clamped = await hr.get(
            "/api/v1/recruitment/candidates", params={"page": 0, "page_size": 1000}
        )
        paged = await hr.get(
            "/api/v1/recruitment/candidates",
            params={"page": 2, "page_size": 2},
        )
        unknown_job = await hr.get(
            "/api/v1/recruitment/candidates",
            params={"job_id": "00000000-0000-4000-8000-0000000000ff"},
        )
        foreign_job = await hr.get(
            "/api/v1/recruitment/candidates", params={"job_id": refs_b[0]}
        )
        invalid_status = await hr.get(
            "/api/v1/recruitment/candidates", params={"status": "hired-away"}
        )

    assert everything.status_code == 200
    assert everything.json()["page"]["total_items"] == 3
    assert [item["id"] for item in by_job.json()["items"]] == [
        second["id"],
        first["id"],
    ]
    assert [item["id"] for item in by_status.json()["items"]] == [second["id"]]
    assert [item["id"] for item in by_first.json()["items"]] == [second["id"]]
    assert [item["id"] for item in by_last.json()["items"]] == [first["id"]]
    assert clamped.json()["page"]["page"] == 1
    assert clamped.json()["page"]["page_size"] == 100
    assert paged.json()["page"]["page"] == 2
    assert len(paged.json()["items"]) == 1
    assert paged.json()["page"]["total_pages"] == 2
    _assert_error(unknown_job, 404, "job_not_found")
    _assert_error(foreign_job, 404, "job_not_found")
    _assert_error(invalid_status, 422, "validation_error")
    assert third["job_id"] == other_job["id"]


async def test_manager_candidate_scope_comes_from_interview_assignment(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        interviewer = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs, first_name="Grace", email="grace.hopper@example.test"
                ),
            )
        ).json()
    interviewer_id = UUID(interviewer["id"])

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        assigned = (await _create_candidate(hr, job["id"])).json()
        other = (
            await _create_candidate(hr, job["id"], email="other@example.test")
        ).json()

    # No schedule API yet (T5): seed the assignment that grants visibility.
    session.add(
        recruitment_models.Interview(
            id=uuid7(),
            tenant_id=TENANT_A,
            candidate_id=UUID(assigned["id"]),
            job_id=UUID(job["id"]),
            interviewer_id=interviewer_id,
            scheduled_at=CLOCK,
            status="scheduled",
        )
    )
    await session.flush()

    async with client_factory(
        identity(role=Role.MANAGER, employee_id=interviewer_id)
    ) as manager:
        listed = await manager.get("/api/v1/recruitment/candidates")
        assigned_detail = await manager.get(
            f"/api/v1/recruitment/candidates/{assigned['id']}"
        )
        other_detail = await manager.get(
            f"/api/v1/recruitment/candidates/{other['id']}"
        )
        by_job = await manager.get(
            "/api/v1/recruitment/candidates", params={"job_id": job["id"]}
        )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [assigned["id"]]
    assert assigned_detail.status_code == 200
    assert assigned_detail.json()["interviews"] == []
    _assert_error(other_detail, 404, "candidate_not_found")
    assert [item["id"] for item in by_job.json()["items"]] == [assigned["id"]]

    for role in (Role.EMPLOYEE, Role.FINANCE):
        async with client_factory(
            identity(role=role, employee_id=interviewer_id)
        ) as client:
            denied_list = await client.get("/api/v1/recruitment/candidates")
            denied_detail = await client.get(
                f"/api/v1/recruitment/candidates/{assigned['id']}"
            )
        _assert_error(denied_list, 403, "recruitment_denied")
        _assert_error(denied_detail, 403, "recruitment_denied")


async def test_recruitment_candidates_are_tenant_isolated(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as tenant_a_admin:
        refs_a = await create_org_refs(tenant_a_admin)
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin, suffix=" B")

    async with client_factory(_hr()) as tenant_a_hr:
        job = await _create_open_job(tenant_a_hr, refs_a)
        candidate = (await _create_candidate(tenant_a_hr, job["id"])).json()
    candidate_id = candidate["id"]

    async with client_factory(identity(tenant=TENANT_B, role=Role.HR)) as tenant_b_hr:
        detail = await tenant_b_hr.get(f"/api/v1/recruitment/candidates/{candidate_id}")
        update = await tenant_b_hr.patch(
            f"/api/v1/recruitment/candidates/{candidate_id}",
            json={"first_name": "Nope"},
        )
        advance = await tenant_b_hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/advance"
        )
        reject = await tenant_b_hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/reject", json={}
        )
        withdraw = await tenant_b_hr.post(
            f"/api/v1/recruitment/candidates/{candidate_id}/withdraw", json={}
        )
        listed = await tenant_b_hr.get("/api/v1/recruitment/candidates")
        foreign_job = await _create_candidate(
            tenant_b_hr, job["id"], email="other@example.test"
        )
        # The same email is a separate application in another tenant.
        own_job = await _create_open_job(tenant_b_hr, refs_b, title="B job")
        own = await _create_candidate(
            tenant_b_hr, own_job["id"], email="ada@example.test"
        )

    _assert_error(detail, 404, "candidate_not_found")
    _assert_error(update, 404, "candidate_not_found")
    _assert_error(advance, 404, "candidate_not_found")
    _assert_error(reject, 404, "candidate_not_found")
    _assert_error(withdraw, 404, "candidate_not_found")
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    _assert_error(foreign_job, 404, "job_not_found")
    assert own.status_code == 201
    assert own.json()["tenant_id"] == str(TENANT_B)
