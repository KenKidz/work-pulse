"""HTTP integration tests for the recruitment hire seam (T4, ADR 0020).

Every test drives the real router, service, PostgreSQL adapters and the real
`PostgresEmployeeHirePort` through the shared session. The router clock is
injected (`recruitment_api._now`), so hire stamps never depend on wall time.

Covered here (`.ai/features/recruitment.md` → T4):

- `offered → hired` creates the Employee + first employment period through the
  Employees service, emits both `employee.created` and `candidate.hired` in the
  same outbox, and audits the hire without PII;
- hire overrides and `employee_code` reach the Employee record;
- seam failures propagate unchanged and leave the candidate `OFFERED` with no
  employee row, no `candidate.hired` and no hire audit;
- `openings` capacity (`job_openings_filled`), closed jobs (`job_not_open`),
  wrong candidate stage (`candidate_stage_conflict`) and future dates (`422`);
- role gate (`403 recruitment_denied`) and tenant isolation (`404`).
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import recruitment as recruitment_api
from app.dependencies import get_recruitment_service
from app.domains.employees import models as employees_orm
from app.domains.employees.schemas import EmploymentType
from app.domains.recruitment import models as recruitment_models
from app.domains.recruitment.repository import EmployeeHirePayload
from app.domains.recruitment.schemas import HireCandidate
from app.infrastructure.postgres.engine import get_session_factory
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.recruitment_hire import PostgresEmployeeHirePort
from app.infrastructure.postgres.session import get_session
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
PINNED_HIRE_KEYS = {
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
    "interviews",
}
MISSING = "00000000-0000-4000-8000-0000000000ff"


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


def _hire_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "employment_type": "full_time",
        "hired_at": "2026-10-20",
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


async def _offered_candidate(
    client: AsyncClient, refs: tuple[str, str, str], **overrides: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    job = await _create_open_job(client, refs)
    candidate = await _create_candidate(client, job["id"], **overrides)
    for _ in range(2):
        advanced = await client.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/advance"
        )
        assert advanced.status_code == 200
    return job, candidate


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(select(AuditEntry).order_by(AuditEntry.id))
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _hr() -> Any:
    return identity(role=Role.HR)


async def test_hire_creates_employee_events_and_audit_without_pii(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job, candidate = await _offered_candidate(hr, refs)
        hired = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/hire",
            json=_hire_payload(),
        )

    assert hired.status_code == 200
    body = hired.json()
    assert set(body) == PINNED_HIRE_KEYS
    assert body["status"] == "hired"
    assert body["employee_id"] is not None
    assert body["hired_at"] in (CLOCK.isoformat(), "2026-10-23T12:00:00Z")
    assert body["interviews"] == []

    employee_id = body["employee_id"]
    async with client_factory() as admin:
        employee = await admin.get(f"/api/v1/employees/{employee_id}")

    assert employee.status_code == 200
    detail = employee.json()
    assert detail["tenant_id"] == str(TENANT_A)
    assert detail["first_name"] == "Ada"
    assert detail["last_name"] == "Lovelace"
    assert detail["email"] == "ada@example.test"
    assert detail["phone"] == "+44 20 7946 0000"
    assert detail["department_id"] == refs[0]
    assert detail["position_id"] == refs[1]
    assert detail["location_id"] == refs[2]
    assert detail["employment_type"] == "full_time"
    assert detail["hired_at"] == "2026-10-20"
    assert detail["status"] == "active"
    assert detail["current_employment"] is not None
    assert detail["current_employment"]["effective_from"] == "2026-10-20"
    assert detail["current_employment"]["effective_to"] is None

    outbox = await _outbox_rows(session)
    by_type: dict[str, list[EventOutboxRow]] = {}
    for row in outbox:
        by_type.setdefault(row.event_type, []).append(row)

    assert len(by_type["candidate.hired"]) == 1
    hired_event = by_type["candidate.hired"][0]
    assert hired_event.payload["actor_id"] == str(USER)
    assert hired_event.payload["data"] == {
        "candidate_id": candidate["id"],
        "job_id": job["id"],
        "employee_id": employee_id,
        "hired_by": str(USER),
    }
    assert len(by_type["employee.created"]) == 1
    assert by_type["employee.created"][0].payload["data"]["employee_id"] == employee_id

    audit = await _audit_rows(session)
    hire_audit = [row for row in audit if row.action == "candidate.hired"]
    assert len(hire_audit) == 1
    assert hire_audit[0].entity_id == UUID(candidate["id"])
    assert hire_audit[0].actor_id == USER
    assert hire_audit[0].changes == {
        "status": {"old": "offered", "new": "hired"},
        "employee_id": {"old": None, "new": employee_id},
        "hired_at": {"old": None, "new": CLOCK.isoformat()},
    }

    # PII never reaches the bus or audit values (ids/state/dates only).
    rendered = " ".join(str(row.payload) for row in outbox)
    rendered += " ".join(str(row.changes) for row in audit)
    for pii in ("Ada", "Lovelace", "ada@example.test", "7946", "Grace"):
        assert pii not in rendered


async def test_hire_applies_overrides_and_employee_code(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        manager = (
            await admin.post(
                "/api/v1/employees",
                json=employee_payload(
                    refs, first_name="Grace", email="grace.hopper@example.test"
                ),
            )
        ).json()

    async with client_factory(_hr()) as hr:
        _, candidate = await _offered_candidate(hr, refs)
        hired = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/hire",
            json=_hire_payload(
                employment_type="contract",
                hired_at="2026-09-15",
                manager_id=manager["id"],
                employee_code="EMP-77777",
                first_name="Augusta",
                last_name="King",
                email="augusta@example.test",
                phone="+1 555 0100",
            ),
        )

    assert hired.status_code == 200
    employee_id = hired.json()["employee_id"]

    async with client_factory() as admin:
        employee = (await admin.get(f"/api/v1/employees/{employee_id}")).json()

    assert employee["first_name"] == "Augusta"
    assert employee["last_name"] == "King"
    assert employee["email"] == "augusta@example.test"
    assert employee["phone"] == "+1 555 0100"
    assert employee["employee_code"] == "EMP-77777"
    assert employee["manager_id"] == manager["id"]
    assert employee["employment_type"] == "contract"
    assert employee["hired_at"] == "2026-09-15"

    # The original candidate PII is not on the bus even though it was the
    # default before the override.
    outbox = await _outbox_rows(session)
    rendered = " ".join(str(row.payload) for row in outbox)
    assert "Ada" not in rendered
    assert "ada@example.test" not in rendered
    assert "Augusta" not in rendered
    assert "augusta@example.test" not in rendered


async def test_hire_email_taken_rolls_back_and_keeps_candidate_offered(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
        await admin.post(
            "/api/v1/employees",
            json=employee_payload(refs, email="ada@example.test"),
        )

    async with client_factory(_hr()) as hr:
        _, candidate = await _offered_candidate(hr, refs)
        failed = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/hire",
            json=_hire_payload(),
        )
        after = await hr.get(f"/api/v1/recruitment/candidates/{candidate['id']}")

    _assert_error(failed, 409, "employee_email_taken")
    assert after.status_code == 200
    assert after.json()["status"] == "offered"
    assert after.json()["employee_id"] is None

    async with client_factory() as admin:
        employees = await admin.get("/api/v1/employees")
    assert employees.json()["page"]["total_items"] == 1

    outbox = await _outbox_rows(session)
    assert "candidate.hired" not in {row.event_type for row in outbox}
    assert [row.event_type for row in outbox].count("employee.created") == 1

    audit = await _audit_rows(session)
    assert "candidate.hired" not in {row.action for row in audit}


async def test_hire_manager_invalid_rolls_back_and_keeps_candidate_offered(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        _, candidate = await _offered_candidate(hr, refs)
        failed = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/hire",
            json=_hire_payload(manager_id=MISSING),
        )
        after = await hr.get(f"/api/v1/recruitment/candidates/{candidate['id']}")

    _assert_error(failed, 400, "employee_manager_invalid")
    assert after.json()["status"] == "offered"
    assert after.json()["employee_id"] is None

    async with client_factory() as admin:
        employees = await admin.get("/api/v1/employees")
    assert employees.json()["page"]["total_items"] == 0

    outbox = await _outbox_rows(session)
    assert "employee.created" not in {row.event_type for row in outbox}
    assert "candidate.hired" not in {row.event_type for row in outbox}


async def test_post_write_seam_failure_rolls_back_the_employee(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam writes the Employee, then fails: the transaction leaves nothing.

    The email/code/manager failures above happen before any Employee write.
    This test exercises the ADR 0020 same-session guarantee itself: the real
    port creates the Employee on the request session, a post-write failure
    forces the request-session rollback, and a fresh session sees no employee,
    no `employee.created` and no hire audit.
    """
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)
    async with client_factory(_hr()) as hr:
        _, candidate = await _offered_candidate(hr, refs)
    # The setup must be committed before the production rollback path runs.
    await session.commit()

    original = PostgresEmployeeHirePort.create_employee

    async def create_then_fail(
        self: PostgresEmployeeHirePort,
        tenant_id: UUID,
        actor_id: UUID,
        payload: EmployeeHirePayload,
        now: datetime,
    ) -> UUID:
        await original(self, tenant_id, actor_id, payload, now)
        raise RuntimeError("post-write hire failure")

    monkeypatch.setattr(PostgresEmployeeHirePort, "create_employee", create_then_fail)

    # `get_session` is torn down with `athrow` on a failing request, which is
    # the production rollback path (the same pattern as the outbox tests).
    lifecycle = cast(AsyncGenerator[AsyncSession, None], get_session())
    hire_session = await anext(lifecycle)
    service = get_recruitment_service(hire_session)
    with pytest.raises(RuntimeError, match="post-write hire failure"):
        await service.hire_candidate(
            identity(role=Role.HR),
            UUID(candidate["id"]),
            HireCandidate(
                employment_type=EmploymentType.FULL_TIME,
                hired_at=CLOCK.date(),
            ),
            CLOCK,
        )
    with pytest.raises(RuntimeError, match="post-write hire failure"):
        await lifecycle.athrow(RuntimeError("post-write hire failure"))

    async with get_session_factory()() as fresh:
        employees = list(
            (await fresh.execute(select(employees_orm.Employee))).scalars()
        )
        assert employees == []

        events = list(
            (
                await fresh.execute(
                    select(EventOutboxRow).where(
                        EventOutboxRow.event_type.in_(
                            ("employee.created", "candidate.hired")
                        )
                    )
                )
            ).scalars()
        )
        assert events == []

        audits = list(
            (
                await fresh.execute(
                    select(AuditEntry).where(
                        AuditEntry.action.in_(("employee.created", "candidate.hired"))
                    )
                )
            ).scalars()
        )
        assert audits == []

        stored = (
            await fresh.execute(
                select(recruitment_models.Candidate).where(
                    recruitment_models.Candidate.id == UUID(candidate["id"])
                )
            )
        ).scalar_one()
        assert stored.status == "offered"
        assert stored.employee_id is None


async def test_hire_respects_job_openings(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs, openings=1)
        first = await _create_candidate(hr, job["id"])
        second = await _create_candidate(hr, job["id"], email="grace@example.test")
        for candidate in (first, second):
            for _ in range(2):
                await hr.post(
                    f"/api/v1/recruitment/candidates/{candidate['id']}/advance"
                )

        first_hire = await hr.post(
            f"/api/v1/recruitment/candidates/{first['id']}/hire",
            json=_hire_payload(),
        )
        second_hire = await hr.post(
            f"/api/v1/recruitment/candidates/{second['id']}/hire",
            json=_hire_payload(),
        )
        after = await hr.get(f"/api/v1/recruitment/candidates/{second['id']}")

    assert first_hire.status_code == 200
    _assert_error(second_hire, 409, "job_openings_filled")
    assert after.json()["status"] == "offered"
    assert after.json()["employee_id"] is None

    async with client_factory() as admin:
        employees = await admin.get("/api/v1/employees")
    assert employees.json()["page"]["total_items"] == 1

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox].count("candidate.hired") == 1


async def test_hire_failures_and_denied_roles(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job = await _create_open_job(hr, refs)
        applied = await _create_candidate(hr, job["id"])
        interviewed = await _create_candidate(hr, job["id"], email="grace@example.test")
        await hr.post(f"/api/v1/recruitment/candidates/{interviewed['id']}/advance")
        offered = await _create_candidate(hr, job["id"], email="kj@example.test")
        for _ in range(2):
            await hr.post(f"/api/v1/recruitment/candidates/{offered['id']}/advance")

        applied_hire = await hr.post(
            f"/api/v1/recruitment/candidates/{applied['id']}/hire",
            json=_hire_payload(),
        )
        interviewing_hire = await hr.post(
            f"/api/v1/recruitment/candidates/{interviewed['id']}/hire",
            json=_hire_payload(),
        )
        future_hire = await hr.post(
            f"/api/v1/recruitment/candidates/{offered['id']}/hire",
            json=_hire_payload(hired_at="2026-10-24"),
        )
        missing_hire = await hr.post(
            f"/api/v1/recruitment/candidates/{MISSING}/hire",
            json=_hire_payload(),
        )

    _assert_error(applied_hire, 409, "candidate_stage_conflict")
    _assert_error(interviewing_hire, 409, "candidate_stage_conflict")
    _assert_error(future_hire, 422, "validation_error")
    _assert_error(missing_hire, 404, "candidate_not_found")

    for role in (Role.EMPLOYEE, Role.MANAGER, Role.FINANCE):
        async with client_factory(identity(role=role)) as client:
            denied = await client.post(
                f"/api/v1/recruitment/candidates/{offered['id']}/hire",
                json=_hire_payload(),
            )
        _assert_error(denied, 403, "recruitment_denied")


async def test_hire_on_a_closed_job_is_refused(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as admin:
        refs = await create_org_refs(admin)

    async with client_factory(_hr()) as hr:
        job, candidate = await _offered_candidate(hr, refs)
        await hr.post(f"/api/v1/recruitment/jobs/{job['id']}/close")
        hired = await hr.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/hire",
            json=_hire_payload(),
        )
        after = await hr.get(f"/api/v1/recruitment/candidates/{candidate['id']}")

    _assert_error(hired, 409, "job_not_open")
    assert after.json()["status"] == "offered"


async def test_hire_is_tenant_isolated(
    client_factory: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recruitment_api, "_now", lambda: CLOCK)

    async with client_factory() as tenant_a_admin:
        refs_a = await create_org_refs(tenant_a_admin)
    async with client_factory(identity(tenant=TENANT_B)) as tenant_b_admin:
        await create_org_refs(tenant_b_admin, suffix=" B")

    async with client_factory(_hr()) as tenant_a_hr:
        _, candidate = await _offered_candidate(tenant_a_hr, refs_a)

    async with client_factory(identity(tenant=TENANT_B, role=Role.HR)) as tenant_b_hr:
        hired = await tenant_b_hr.post(
            f"/api/v1/recruitment/candidates/{candidate['id']}/hire",
            json=_hire_payload(),
        )

    _assert_error(hired, 404, "candidate_not_found")
    assert candidate["tenant_id"] == str(TENANT_A)
