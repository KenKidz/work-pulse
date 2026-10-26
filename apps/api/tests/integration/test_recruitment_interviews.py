"""HTTP integration tests for recruitment interviews (T5).

Every test drives the real router, service and PostgreSQL adapter through the
shared session. The router clock is injected (`recruitment_api._now`), so
stamps never depend on wall time.

Covered here (`.ai/features/recruitment.md` → T5):

- schedule → `201 scheduled` + `interview.scheduled` (identifiers only) +
  PII-safe audit; update (reschedule/re-assign/re-note) and cancel with
  `interview.cancelled`;
- the assigned interviewer completes with feedback (`interview.completed`,
  `rating`/`feedback_notes` stored, candidate stage unchanged), and the scoped
  viewer sees only their own interviews/candidate detail;
- pinned failures: `candidate_stage_conflict`, `interviewer_not_found`,
  `interviewer_not_active`, `interview_not_scheduled`, `interview_not_found`,
  denied roles (`403 recruitment_denied`), non-assignee `403`/`404`;
- list filters (`candidate_id`, `job_id`, `status`, `mine`, `interviewer_id`,
  `from`/`to`), clamped pagination and foreign filter ids `404`;
- tenant isolation: B sees and touches nothing of A's.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

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
    employee_payload,
    identity,
)

CLOCK = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
INTERVIEW_AT = "2026-10-25T09:00:00Z"
LATER_AT = "2026-10-27T14:30:00Z"
MISSING = "00000000-0000-4000-8000-0000000000ff"
PINNED_INTERVIEW_KEYS = {
    "id",
    "tenant_id",
    "candidate_id",
    "job_id",
    "interviewer_id",
    "scheduled_at",
    "notes",
    "status",
    "rating",
    "feedback_notes",
    "completed_at",
    "cancelled_at",
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


def _interview_payload(interviewer_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "interviewer_id": interviewer_id,
        "scheduled_at": INTERVIEW_AT,
        "notes": "Panel room 2",
    }
    payload.update(overrides)
    return payload


async def _create_open_job(
    client: AsyncClient, refs: tuple[str, str, str], **overrides: object
) -> dict[str, Any]:
    job = (
        await client.post(
            "/api/v1/recruitment/jobs", json=_job_payload(refs, **overrides)
        )
    ).json()
    opened = await client.post(f"/api/v1/recruitment/jobs/{job['id']}/open")
    assert opened.status_code == 200
    return job


async def _create_candidate(
    client: AsyncClient, job_id: str, **overrides: object
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/recruitment/jobs/{job_id}/candidates",
        json=_candidate_payload(**overrides),
    )
    assert response.status_code == 201
    return response.json()


async def _interviewing_candidate(
    client: AsyncClient, refs: tuple[str, str, str], **overrides: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    job = await _create_open_job(client, refs)
    candidate = await _create_candidate(client, job["id"], **overrides)
    advanced = await client.post(
        f"/api/v1/recruitment/candidates/{candidate['id']}/advance"
    )
    assert advanced.status_code == 200
    return job, candidate


async def _create_employee(
    client: AsyncClient,
    refs: tuple[str, str, str],
    *,
    first_name: str,
    email: str,
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/employees",
        json=employee_payload(refs, first_name=first_name, email=email),
    )
    assert response.status_code == 201
    return response.json()


async def _schedule(
    client: AsyncClient, candidate_id: str, interviewer_id: str, **overrides: object
) -> Response:
    return await client.post(
        f"/api/v1/recruitment/candidates/{candidate_id}/interviews",
        json=_interview_payload(interviewer_id, **overrides),
    )


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == "interview")
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _hr() -> Any:
    return identity(role=Role.HR)


def _manager(employee_id: str) -> Any:
    return identity(role=Role.MANAGER, employee_id=UUID(employee_id))


async def test_interview_lifecycle_events_audit_and_scoping(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        interviewer = await _create_employee(
            admin,
            refs,
            first_name="Grace",
            email="grace.hopper@example.test",
        )
        peer = await _create_employee(
            admin, refs, first_name="Katherine", email="kj@example.test"
        )
    interviewer_id = interviewer["id"]

    async with client_factory(_hr()) as hr:
        job, candidate = await _interviewing_candidate(hr, refs)
        scheduled = await _schedule(
            hr, candidate["id"], interviewer_id, notes="Panel room 2"
        )
        interview = scheduled.json()
        all_items = await hr.get("/api/v1/recruitment/interviews")
        by_candidate = await hr.get(
            "/api/v1/recruitment/interviews",
            params={"candidate_id": candidate["id"]},
        )
        by_job = await hr.get(
            "/api/v1/recruitment/interviews", params={"job_id": job["id"]}
        )
        by_status = await hr.get(
            "/api/v1/recruitment/interviews", params={"status": "scheduled"}
        )
        by_interviewer = await hr.get(
            "/api/v1/recruitment/interviews",
            params={"interviewer_id": interviewer_id},
        )
        on_day = await hr.get(
            "/api/v1/recruitment/interviews",
            params={"from": "2026-10-25", "to": "2026-10-25"},
        )
        outside = await hr.get(
            "/api/v1/recruitment/interviews", params={"from": "2026-10-26"}
        )
        mine_hr = await hr.get("/api/v1/recruitment/interviews", params={"mine": 1})
        clamped = await hr.get(
            "/api/v1/recruitment/interviews",
            params={"page": 0, "page_size": 1000},
        )

    assert scheduled.status_code == 201
    assert set(interview) == PINNED_INTERVIEW_KEYS
    assert interview["tenant_id"] == str(TENANT_A)
    assert interview["candidate_id"] == candidate["id"]
    assert interview["job_id"] == job["id"]
    assert interview["interviewer_id"] == interviewer_id
    assert interview["scheduled_at"].startswith("2026-10-25T09:00:00")
    assert interview["status"] == "scheduled"
    assert interview["notes"] == "Panel room 2"
    assert interview["rating"] is None
    assert interview["feedback_notes"] is None
    assert interview["completed_at"] is None
    assert interview["cancelled_at"] is None

    assert [item["id"] for item in all_items.json()["items"]] == [interview["id"]]
    assert [item["id"] for item in by_candidate.json()["items"]] == [interview["id"]]
    assert [item["id"] for item in by_job.json()["items"]] == [interview["id"]]
    assert [item["id"] for item in by_status.json()["items"]] == [interview["id"]]
    assert [item["id"] for item in by_interviewer.json()["items"]] == [interview["id"]]
    assert [item["id"] for item in on_day.json()["items"]] == [interview["id"]]
    assert outside.json()["items"] == []
    assert mine_hr.json()["items"] == []  # HR's employee link is not assigned
    assert clamped.json()["page"]["page"] == 1
    assert clamped.json()["page"]["page_size"] == 100

    async with client_factory(_manager(interviewer_id)) as manager:
        mine = await manager.get("/api/v1/recruitment/interviews", params={"mine": 1})
        forced = await manager.get("/api/v1/recruitment/interviews")
        denied_filter = await manager.get(
            "/api/v1/recruitment/interviews",
            params={"interviewer_id": peer["id"]},
        )
        candidate_detail = await manager.get(
            f"/api/v1/recruitment/candidates/{candidate['id']}"
        )
        completed = await manager.post(
            f"/api/v1/recruitment/interviews/{interview['id']}/complete",
            json={"rating": 4, "notes": "Strong systems answer"},
        )

    assert [item["id"] for item in mine.json()["items"]] == [interview["id"]]
    assert [item["id"] for item in forced.json()["items"]] == [interview["id"]]
    _assert_error(denied_filter, 403, "recruitment_denied")
    assert candidate_detail.status_code == 200
    assert [item["id"] for item in candidate_detail.json()["interviews"]] == [
        interview["id"]
    ]

    assert completed.status_code == 200
    body = completed.json()
    assert body["status"] == "completed"
    assert body["rating"] == 4
    assert body["feedback_notes"] == "Strong systems answer"
    assert body["completed_at"] is not None
    assert body["completed_at"].startswith("2026-10-23T12:00:00")

    # The candidate's stage is unchanged (stage moves stay HR-driven).
    async with client_factory(_hr()) as hr:
        candidate_after = await hr.get(
            f"/api/v1/recruitment/candidates/{candidate['id']}"
        )
        repeat = await hr.patch(
            f"/api/v1/recruitment/interviews/{interview['id']}",
            json={"notes": "Too late"},
        )
        cancel_completed = await hr.post(
            f"/api/v1/recruitment/interviews/{interview['id']}/cancel", json={}
        )

    assert candidate_after.json()["status"] == "interviewing"
    assert candidate_after.json()["interviews"][0]["status"] == "completed"
    _assert_error(repeat, 409, "interview_not_scheduled")
    _assert_error(cancel_completed, 409, "interview_not_scheduled")

    async with client_factory(_manager(interviewer_id)) as manager:
        complete_again = await manager.post(
            f"/api/v1/recruitment/interviews/{interview['id']}/complete",
            json={"rating": 5},
        )
    _assert_error(complete_again, 409, "interview_not_scheduled")

    outbox = [
        row
        for row in await _outbox_rows(session)
        if row.payload.get("data", {}).get("interview_id") == interview["id"]
    ]
    assert [row.event_type for row in outbox] == [
        "interview.scheduled",
        "interview.completed",
    ]
    assert outbox[0].payload["actor_id"] == str(USER)
    assert outbox[0].payload["data"] == {
        "interview_id": interview["id"],
        "candidate_id": candidate["id"],
        "job_id": job["id"],
        "interviewer_id": interviewer_id,
    }
    assert outbox[1].payload["data"] == {
        "interview_id": interview["id"],
        "candidate_id": candidate["id"],
        "job_id": job["id"],
        "interviewer_id": interviewer_id,
        "completed_by": str(USER),
    }

    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "interview.scheduled",
        "interview.completed",
    ]
    assert audit[0].changes is not None
    assert audit[0].changes["status"] == {"old": None, "new": "scheduled"}
    assert audit[0].changes["notes"] == "changed"
    assert audit[1].changes == {
        "status": {"old": "scheduled", "new": "completed"},
        "rating": {"old": None, "new": 4},
        "completed_at": {"old": None, "new": CLOCK.isoformat()},
        "feedback_notes": "changed",
    }

    # PII never reaches the bus or audit values.
    rendered = " ".join(str(row.payload) for row in outbox)
    rendered += " ".join(str(row.changes) for row in audit)
    for pii in (
        "Panel room 2",
        "Strong systems answer",
        "Grace",
        "Katherine",
        "example.test",
    ):
        assert pii not in rendered


async def test_interview_update_and_cancel(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        first = await _create_employee(
            admin, refs, first_name="Grace", email="grace.hopper@example.test"
        )
        second = await _create_employee(
            admin, refs, first_name="Katherine", email="kj@example.test"
        )

    async with client_factory(_hr()) as hr:
        _, candidate = await _interviewing_candidate(hr, refs)
        interview = (await _schedule(hr, candidate["id"], first["id"])).json()

        updated = await hr.patch(
            f"/api/v1/recruitment/interviews/{interview['id']}",
            json={
                "interviewer_id": second["id"],
                "scheduled_at": LATER_AT,
                "notes": "Moved to room 4",
            },
        )
        cancelled = await hr.post(
            f"/api/v1/recruitment/interviews/{interview['id']}/cancel",
            json={"note": "Candidate requested a move"},
        )
        cancel_again = await hr.post(
            f"/api/v1/recruitment/interviews/{interview['id']}/cancel", json={}
        )
        rescheduled = await _schedule(hr, candidate["id"], first["id"])

    assert updated.status_code == 200
    body = updated.json()
    assert body["interviewer_id"] == second["id"]
    assert body["scheduled_at"].startswith("2026-10-27T14:30:00")
    assert body["notes"] == "Moved to room 4"
    assert body["status"] == "scheduled"

    assert cancelled.status_code == 200
    cancelled_body = cancelled.json()
    assert cancelled_body["status"] == "cancelled"
    assert cancelled_body["cancelled_at"] is not None
    assert cancelled_body["notes"] == "Candidate requested a move"
    _assert_error(cancel_again, 409, "interview_not_scheduled")

    # A cancelled interview cannot be completed by its (former) interviewer.
    async with client_factory(_manager(second["id"])) as manager:
        complete_cancelled = await manager.post(
            f"/api/v1/recruitment/interviews/{interview['id']}/complete",
            json={"rating": 4},
        )
    _assert_error(complete_cancelled, 409, "interview_not_scheduled")

    assert rescheduled.status_code == 201
    assert rescheduled.json()["status"] == "scheduled"

    outbox = [
        row
        for row in await _outbox_rows(session)
        if row.payload.get("data", {}).get("interview_id") == interview["id"]
    ]
    assert [row.event_type for row in outbox] == [
        "interview.scheduled",
        "interview.cancelled",
    ]
    assert outbox[1].payload["data"] == {
        "interview_id": interview["id"],
        "candidate_id": candidate["id"],
        "job_id": candidate["job_id"],
        "cancelled_by": str(USER),
    }

    audit = [
        row
        for row in await _audit_rows(session)
        if row.entity_id == UUID(interview["id"])
    ]
    assert [row.action for row in audit] == [
        "interview.scheduled",
        "interview.updated",
        "interview.cancelled",
    ]
    assert audit[1].changes is not None
    assert audit[1].changes["interviewer_id"] == {
        "old": first["id"],
        "new": second["id"],
    }
    assert audit[1].changes["notes"] == "changed"
    assert audit[2].changes == {
        "status": {"old": "scheduled", "new": "cancelled"},
        "cancelled_at": {"old": None, "new": CLOCK.isoformat()},
        "notes": "changed",
    }
    rendered = " ".join(str(row.payload) for row in outbox)
    rendered += " ".join(str(row.changes) for row in audit)
    for pii in ("Moved to room 4", "Candidate requested a move"):
        assert pii not in rendered


async def test_interview_failures_and_denied_roles(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        interviewer = await _create_employee(
            admin, refs, first_name="Grace", email="grace.hopper@example.test"
        )
        peer = await _create_employee(
            admin, refs, first_name="Katherine", email="kj@example.test"
        )
        terminated = await _create_employee(
            admin, refs, first_name="Cy", email="cy.diaz@example.test"
        )
        terminated_response = await admin.post(
            f"/api/v1/employees/{terminated['id']}/termination",
            json={"effective_date": "2026-10-01", "reason": "left"},
        )
        assert terminated_response.status_code == 200

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        applied = await _create_candidate(hr, job["id"])
        interviewing = await _create_candidate(
            hr, job["id"], email="grace@example.test"
        )
        await hr.post(f"/api/v1/recruitment/candidates/{interviewing['id']}/advance")

        schedule_applied = await _schedule(hr, applied["id"], interviewer["id"])
        schedule_missing_candidate = await _schedule(hr, MISSING, interviewer["id"])
        schedule_missing_interviewer = await _schedule(hr, interviewing["id"], MISSING)
        schedule_terminated = await _schedule(hr, interviewing["id"], terminated["id"])
        schedule_bad_time = await hr.post(
            f"/api/v1/recruitment/candidates/{interviewing['id']}/interviews",
            json={
                "interviewer_id": interviewer["id"],
                "scheduled_at": "not-a-date",
            },
        )

        scheduled = await _schedule(hr, interviewing["id"], interviewer["id"])
        interview_id = scheduled.json()["id"]
        # HR has management standing but is not the assignee: 403.
        complete_hr = await hr.post(
            f"/api/v1/recruitment/interviews/{interview_id}/complete",
            json={"rating": 4},
        )
        complete_missing = await hr.post(
            f"/api/v1/recruitment/interviews/{MISSING}/complete",
            json={"rating": 4},
        )
        update_missing = await hr.patch(
            f"/api/v1/recruitment/interviews/{MISSING}", json={"notes": "x"}
        )
        cancel_missing = await hr.post(
            f"/api/v1/recruitment/interviews/{MISSING}/cancel", json={}
        )
        unknown_candidate_filter = await hr.get(
            "/api/v1/recruitment/interviews", params={"candidate_id": MISSING}
        )
        unknown_job_filter = await hr.get(
            "/api/v1/recruitment/interviews", params={"job_id": MISSING}
        )
        unknown_interviewer_filter = await hr.get(
            "/api/v1/recruitment/interviews", params={"interviewer_id": MISSING}
        )

    assert scheduled.status_code == 201
    _assert_error(schedule_applied, 409, "candidate_stage_conflict")
    _assert_error(schedule_missing_candidate, 404, "candidate_not_found")
    _assert_error(schedule_missing_interviewer, 404, "interviewer_not_found")
    _assert_error(schedule_terminated, 409, "interviewer_not_active")
    _assert_error(schedule_bad_time, 422, "validation_error")
    _assert_error(complete_hr, 403, "recruitment_denied")
    _assert_error(complete_missing, 404, "interview_not_found")
    _assert_error(update_missing, 404, "interview_not_found")
    _assert_error(cancel_missing, 404, "interview_not_found")

    async with client_factory(_manager(interviewer["id"])) as manager:
        bad_rating = await manager.post(
            f"/api/v1/recruitment/interviews/{interview_id}/complete",
            json={"rating": 6},
        )
    _assert_error(bad_rating, 422, "validation_error")

    async with client_factory(_manager(peer["id"])) as manager:
        complete_peer = await manager.post(
            f"/api/v1/recruitment/interviews/{interview_id}/complete",
            json={"rating": 4},
        )
    _assert_error(complete_peer, 404, "interview_not_found")

    for role in (Role.EMPLOYEE, Role.FINANCE):
        async with client_factory(
            identity(role=role, employee_id=UUID(interviewer["id"]))
        ) as client:
            denied_schedule = await _schedule(
                client, interviewing["id"], interviewer["id"]
            )
            denied_list = await client.get("/api/v1/recruitment/interviews")
            denied_complete = await client.post(
                f"/api/v1/recruitment/interviews/{interview_id}/complete",
                json={"rating": 4},
            )
        _assert_error(denied_schedule, 403, "recruitment_denied")
        _assert_error(denied_list, 403, "recruitment_denied")
        _assert_error(denied_complete, 403, "recruitment_denied")

    async with client_factory(_manager(interviewer["id"])) as manager:
        denied_manager_schedule = await _schedule(
            manager, interviewing["id"], interviewer["id"]
        )
    _assert_error(denied_manager_schedule, 403, "recruitment_denied")

    _assert_error(unknown_candidate_filter, 404, "candidate_not_found")
    _assert_error(unknown_job_filter, 404, "job_not_found")
    _assert_error(unknown_interviewer_filter, 404, "interviewer_not_found")

    # Close the fixture's interview so the test leaves no scheduled state.
    async with client_factory(_hr()) as hr:
        cancelled = await hr.post(
            f"/api/v1/recruitment/interviews/{interview_id}/cancel", json={}
        )
    assert cancelled.status_code == 200


async def test_interviews_are_tenant_isolated(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as tenant_a_admin:
        refs_a = await create_org_refs(tenant_a_admin)
        interviewer_a = await _create_employee(
            tenant_a_admin,
            refs_a,
            first_name="Grace",
            email="grace.hopper@example.test",
        )
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        refs_b = await create_org_refs(tenant_b_admin, suffix=" B")

    async with client_factory(_hr()) as tenant_a_hr:
        job_a, candidate_a = await _interviewing_candidate(tenant_a_hr, refs_a)
        interview_a = (
            await _schedule(tenant_a_hr, candidate_a["id"], interviewer_a["id"])
        ).json()

    async with client_factory(identity(tenant=TENANT_B, role=Role.HR)) as tenant_b_hr:
        listed = await tenant_b_hr.get("/api/v1/recruitment/interviews")
        schedule_foreign_candidate = await _schedule(
            tenant_b_hr, candidate_a["id"], interviewer_a["id"]
        )
        complete = await tenant_b_hr.post(
            f"/api/v1/recruitment/interviews/{interview_a['id']}/complete",
            json={"rating": 4},
        )
        update = await tenant_b_hr.patch(
            f"/api/v1/recruitment/interviews/{interview_a['id']}",
            json={"notes": "x"},
        )
        cancel = await tenant_b_hr.post(
            f"/api/v1/recruitment/interviews/{interview_a['id']}/cancel", json={}
        )
        candidate_filter = await tenant_b_hr.get(
            "/api/v1/recruitment/interviews",
            params={"candidate_id": candidate_a["id"]},
        )
        job_filter = await tenant_b_hr.get(
            "/api/v1/recruitment/interviews", params={"job_id": job_a["id"]}
        )
        interviewer_filter = await tenant_b_hr.get(
            "/api/v1/recruitment/interviews",
            params={"interviewer_id": interviewer_a["id"]},
        )

        own_job, own_candidate = await _interviewing_candidate(
            tenant_b_hr, refs_b, email="tenant-b@example.test"
        )
        foreign_interviewer = await _schedule(
            tenant_b_hr, own_candidate["id"], interviewer_a["id"]
        )

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    _assert_error(schedule_foreign_candidate, 404, "candidate_not_found")
    _assert_error(complete, 404, "interview_not_found")
    _assert_error(update, 404, "interview_not_found")
    _assert_error(cancel, 404, "interview_not_found")
    _assert_error(candidate_filter, 404, "candidate_not_found")
    _assert_error(job_filter, 404, "job_not_found")
    _assert_error(interviewer_filter, 404, "interviewer_not_found")
    _assert_error(foreign_interviewer, 404, "interviewer_not_found")
    assert own_job["tenant_id"] == str(TENANT_B)
