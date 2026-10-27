"""HTTP integration tests for performance reviews and feedback (T6).

Every test drives the real router, `ReviewService` and PostgreSQL adapter through
the shared session. The router clock is injected (`performance_api._now`), so
timestamps never depend on wall time.

Covered here (`.ai/features/performance.md` → T6, acceptance criteria 3–5):

- create → `201 draft` + one audit entry + a valid `performance.review_started`
  envelope; the subject cannot resolve the review id until it completes, then
  sees it without the nomination list;
- complete needs a rating (`409 review_incomplete`), stamps `completed_at` and
  writes `performance.review_completed`; a completed review is immutable
  (`409 review_not_draft`); self-review is `403`;
- feedback: a nominated provider sees the draft shell, files exactly one
  comment (`performance.feedback_submitted`), a second try is
  `409 feedback_already_submitted`, the subject gets
  `403 feedback_access_denied`, and cancelling orphans feedback for the author;
- role/scope checks (Employee/Finance denied, manager owner-only mutations),
  list narrowing (`feedback_for_me`, Employee completed-only, `422` when
  combined with a status), clamped pagination and tenant isolation through
  nested ids;
- audit rows carry ids/state and `"changed"` markers only — never titles,
  periods, strengths, improvements, comments or rating values.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import performance as performance_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.domains.identity import models as identity_orm
from app.domains.performance.schemas import ReviewStatus
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
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
TENANT_B_EMPLOYEE = UUID("00000000-0000-4000-8000-0000000000bb")

MANAGER_USER = UUID("00000000-0000-4000-8000-000000000041")
REPORT_USER = UUID("00000000-0000-4000-8000-000000000042")
PEER_USER = UUID("00000000-0000-4000-8000-000000000043")
HR_USER = UUID("00000000-0000-4000-8000-000000000044")

PINNED_REVIEW_KEYS = {
    "id",
    "employee_id",
    "period",
    "rating",
    "strengths",
    "improvements",
    "status",
    "feedback_provider_ids",
    "created_at",
    "updated_at",
    "completed_at",
}

PII_PERIOD = "2026-H1 secret — pii-review-period"
PII_STRENGTHS = "Secret strength — pii-review-strengths"
PII_IMPROVEMENTS = "Secret improvement — pii-review-improvements"
PII_COMMENT = "Private peer comment — pii-feedback-comment"


def _assert_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code


def _manager():
    return identity(
        role=Role.MANAGER, user_id=MANAGER_USER, employee_id=MANAGER_EMPLOYEE
    )


def _hr():
    """Management author without an employee link (nobody is never-self here)."""
    return identity(role=Role.HR, user_id=HR_USER, employee_id=None)


def _subject():
    return identity(
        role=Role.EMPLOYEE, user_id=REPORT_USER, employee_id=REPORT_EMPLOYEE
    )


def _peer():
    return identity(role=Role.EMPLOYEE, user_id=PEER_USER, employee_id=PEER_EMPLOYEE)


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


async def _create_review(
    client: AsyncClient,
    *,
    employee_id: UUID,
    period: str | None = None,
    providers: tuple[UUID, ...] = (),
) -> Response:
    body: dict[str, Any] = {"employee_id": str(employee_id)}
    if period is not None:
        body["period"] = period
    body["feedback_provider_ids"] = [str(provider) for provider in providers]
    return await client.post("/api/v1/performance/reviews", json=body)


async def _rating_patch(
    client: AsyncClient, review_id: str, rating: int = 4
) -> Response:
    return await client.patch(
        f"/api/v1/performance/reviews/{review_id}", json={"rating": rating}
    )


async def _review_audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type.in_(("review", "feedback")))
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


async def test_review_hop_draft_404_then_completed_subject_view(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_manager()) as manager:
        created = await _create_review(
            manager,
            employee_id=REPORT_EMPLOYEE,
            period=PII_PERIOD,
            providers=(PEER_EMPLOYEE,),
        )
    review = created.json()

    # The subject cannot resolve the draft id — no denied leak.
    async with client_factory(_subject()) as subject:
        draft_read = await subject.get(f"/api/v1/performance/reviews/{review['id']}")

    async with client_factory(_manager()) as manager:
        rated = await manager.patch(
            f"/api/v1/performance/reviews/{review['id']}",
            json={
                "rating": 4,
                "strengths": PII_STRENGTHS,
                "improvements": PII_IMPROVEMENTS,
            },
        )
        completed = await manager.post(
            f"/api/v1/performance/reviews/{review['id']}/complete"
        )

    assert created.status_code == 201
    assert set(review) == PINNED_REVIEW_KEYS
    assert review["status"] == "draft"
    assert review["rating"] is None
    assert review["feedback_provider_ids"] == [str(PEER_EMPLOYEE)]

    _assert_error(draft_read, 404, "review_not_found")

    assert rated.status_code == 200
    rated_body = rated.json()
    assert rated_body["rating"] == 4
    assert rated_body["strengths"] == PII_STRENGTHS
    assert completed.status_code == 200
    completed_body = completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["completed_at"] is not None

    async with client_factory(_subject()) as subject:
        subject_read = await subject.get(f"/api/v1/performance/reviews/{review['id']}")
        subject_list = await subject.get("/api/v1/performance/reviews")

    assert subject_read.status_code == 200
    subject_body = subject_read.json()
    assert set(subject_body) == PINNED_REVIEW_KEYS
    assert subject_body["rating"] == 4
    assert subject_body["strengths"] == PII_STRENGTHS
    # Subject-facing serialization never exposes the nomination list.
    assert subject_body["feedback_provider_ids"] is None
    assert [item["id"] for item in subject_list.json()["items"]] == [review["id"]]
    assert subject_list.json()["items"][0]["feedback_provider_ids"] is None

    async with client_factory(_hr()) as hr:
        hr_read = await hr.get(f"/api/v1/performance/reviews/{review['id']}")
    assert hr_read.status_code == 200
    assert hr_read.json()["feedback_provider_ids"] == [str(PEER_EMPLOYEE)]

    audit = await _review_audit_rows(session)
    assert [row.action for row in audit] == [
        "review.started",
        "review.updated",
        "review.completed",
    ]
    assert {row.entity_id for row in audit} == {UUID(review["id"])}
    assert audit[0].changes is not None
    assert audit[0].changes["employee_id"] == {
        "old": None,
        "new": str(REPORT_EMPLOYEE),
    }
    assert audit[0].changes["status"] == {"old": None, "new": "draft"}
    assert audit[0].changes["period"] == "changed"  # PII: marker only
    assert audit[1].changes == {
        "rating": "changed",
        "strengths": "changed",
        "improvements": "changed",
    }
    assert audit[2].changes == {"status": {"old": "draft", "new": "completed"}}
    encoded_audit = str([row.changes for row in audit])
    for pii in (PII_PERIOD, PII_STRENGTHS, PII_IMPROVEMENTS):
        assert pii not in encoded_audit

    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "performance.review_started",
        "performance.review_completed",
    ]
    assert [row.payload["data"] for row in outbox] == [
        {
            "review_id": review["id"],
            "employee_id": str(REPORT_EMPLOYEE),
            "status": "draft",
        },
        {
            "review_id": review["id"],
            "employee_id": str(REPORT_EMPLOYEE),
            "status": "completed",
        },
    ]
    encoded_outbox = str([row.payload for row in outbox])
    for pii in (PII_PERIOD, PII_STRENGTHS, PII_IMPROVEMENTS):
        assert pii not in encoded_outbox


async def test_review_failures_are_pinned_and_write_nothing(
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

    async with client_factory(_manager()) as manager:
        draft = (await _create_review(manager, employee_id=REPORT_EMPLOYEE)).json()
        incomplete = await manager.post(
            f"/api/v1/performance/reviews/{draft['id']}/complete"
        )
        assert (await _rating_patch(manager, draft["id"])).status_code == 200
        assert (
            await manager.post(f"/api/v1/performance/reviews/{draft['id']}/complete")
        ).status_code == 200

        outbox_before = len(await _outbox_rows(session))
        audit_before = len(await _review_audit_rows(session))

        patch_completed = await manager.patch(
            f"/api/v1/performance/reviews/{draft['id']}", json={"rating": 2}
        )
        cancel_completed = await manager.post(
            f"/api/v1/performance/reviews/{draft['id']}/cancel"
        )
        complete_again = await manager.post(
            f"/api/v1/performance/reviews/{draft['id']}/complete"
        )
        self_review = await _create_review(manager, employee_id=MANAGER_EMPLOYEE)
        out_of_range_rating = await manager.patch(
            f"/api/v1/performance/reviews/{draft['id']}", json={"rating": 6}
        )

    _assert_error(incomplete, 409, "review_incomplete")
    _assert_error(patch_completed, 409, "review_not_draft")
    _assert_error(cancel_completed, 409, "review_not_draft")
    _assert_error(complete_again, 409, "review_not_draft")
    _assert_error(self_review, 403, "performance_denied")
    _assert_error(out_of_range_rating, 422, "validation_error")
    assert len(await _outbox_rows(session)) == outbox_before
    assert len(await _review_audit_rows(session)) == audit_before

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        employee_create = await _create_review(employee, employee_id=REPORT_EMPLOYEE)
        employee_patch = await employee.patch(
            f"/api/v1/performance/reviews/{draft['id']}", json={"rating": 3}
        )
    _assert_error(employee_create, 403, "performance_denied")
    _assert_error(employee_patch, 403, "performance_denied")

    async with client_factory(identity(role=Role.FINANCE)) as finance:
        finance_create = await _create_review(finance, employee_id=REPORT_EMPLOYEE)
    _assert_error(finance_create, 403, "performance_denied")

    async with client_factory(_hr()) as hr:
        terminated = await _create_review(hr, employee_id=TERMINATED_EMPLOYEE)
        unknown = await _create_review(hr, employee_id=UNKNOWN_EMPLOYEE)
    _assert_error(terminated, 409, "employee_not_active")
    _assert_error(unknown, 404, "employee_not_found")


async def test_feedback_flow_visibility_and_orphaning(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_manager()) as manager:
        review = (
            await _create_review(
                manager,
                employee_id=REPORT_EMPLOYEE,
                providers=(PEER_EMPLOYEE, ACTOR),
            )
        ).json()

    async with client_factory(_peer()) as peer:
        shell = await peer.get(f"/api/v1/performance/reviews/{review['id']}")
        first = await peer.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": PII_COMMENT},
        )
        second = await peer.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": "second attempt"},
        )

    async with client_factory(identity(role=Role.EMPLOYEE)) as actor:
        other_feedback = await actor.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": "nominated peer input"},
        )

    async with client_factory(_subject()) as subject:
        subject_review = await subject.get(
            f"/api/v1/performance/reviews/{review['id']}"
        )
        subject_feedback = await subject.get(
            f"/api/v1/performance/reviews/{review['id']}/feedback"
        )
        subject_submit = await subject.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": "self feedback"},
        )

    async with client_factory(_manager()) as manager:
        manager_feedback = await manager.get(
            f"/api/v1/performance/reviews/{review['id']}/feedback"
        )

    assert shell.status_code == 200
    shell_body = shell.json()
    assert shell_body["id"] == review["id"]
    assert shell_body["status"] == "draft"
    assert shell_body["rating"] is None
    assert shell_body["period"] is None
    assert shell_body["strengths"] is None
    assert shell_body["improvements"] is None
    assert shell_body["feedback_provider_ids"] is None

    assert first.status_code == 201
    feedback = first.json()
    assert set(feedback) == {
        "id",
        "review_id",
        "author_id",
        "comment",
        "created_at",
    }
    assert feedback["review_id"] == review["id"]
    assert feedback["author_id"] == str(PEER_EMPLOYEE)
    assert feedback["comment"] == PII_COMMENT
    _assert_error(second, 409, "feedback_already_submitted")
    assert other_feedback.status_code == 201
    _assert_error(subject_review, 404, "review_not_found")
    _assert_error(subject_feedback, 403, "feedback_access_denied")
    _assert_error(subject_submit, 403, "performance_denied")
    assert manager_feedback.status_code == 200
    assert len(manager_feedback.json()["items"]) == 2

    async with client_factory(_peer()) as peer:
        peer_feedback = await peer.get(
            f"/api/v1/performance/reviews/{review['id']}/feedback"
        )
    assert [item["id"] for item in peer_feedback.json()["items"]] == [feedback["id"]]

    # Complete the review, then feedback filing is closed.
    async with client_factory(_manager()) as manager:
        assert (await _rating_patch(manager, review["id"])).status_code == 200
        assert (
            await manager.post(f"/api/v1/performance/reviews/{review['id']}/complete")
        ).status_code == 200
        late = await manager.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": "late"},
        )
    _assert_error(late, 403, "performance_denied")

    async with client_factory(_peer()) as peer:
        late_peer = await peer.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": "late peer"},
        )
    _assert_error(late_peer, 409, "review_not_draft")

    # Cancelling a draft leaves its feedback readable to the author.
    async with client_factory(_hr()) as hr:
        cancelled = (
            await _create_review(
                hr,
                employee_id=REPORT_EMPLOYEE,
                providers=(PEER_EMPLOYEE,),
            )
        ).json()
    async with client_factory(_peer()) as peer:
        assert (
            await peer.post(
                f"/api/v1/performance/reviews/{cancelled['id']}/feedback",
                json={"comment": "orphan input"},
            )
        ).status_code == 201
    async with client_factory(_hr()) as hr:
        assert (
            await hr.post(f"/api/v1/performance/reviews/{cancelled['id']}/cancel")
        ).status_code == 200
        orphaned = await hr.get(
            f"/api/v1/performance/reviews/{cancelled['id']}/feedback"
        )
    assert [item["comment"] for item in orphaned.json()["items"]] == ["orphan input"]

    outbox = await _outbox_rows(session)
    feedback_events = [
        row for row in outbox if row.event_type == "performance.feedback_submitted"
    ]
    assert len(feedback_events) == 3
    assert {row.payload["data"]["review_id"] for row in feedback_events} == {
        review["id"],
        cancelled["id"],
    }
    assert {row.payload["data"]["employee_id"] for row in feedback_events} == {
        str(REPORT_EMPLOYEE)
    }
    assert all(
        set(row.payload["data"]) == {"feedback_id", "review_id", "employee_id"}
        for row in feedback_events
    )
    assert PII_COMMENT not in str([row.payload for row in outbox])

    audit = await _review_audit_rows(session)
    feedback_audit = [row for row in audit if row.action == "feedback.submitted"]
    assert len(feedback_audit) == 3
    assert all(
        row.changes is not None and row.changes["comment"] == "changed"
        for row in feedback_audit
    )
    assert PII_COMMENT not in str([row.changes for row in audit])


async def test_provider_churn_blocks_removing_a_provider_who_filed(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_manager()) as manager:
        review = (
            await _create_review(
                manager,
                employee_id=REPORT_EMPLOYEE,
                providers=(PEER_EMPLOYEE,),
            )
        ).json()

    async with client_factory(_peer()) as peer:
        assert (
            await peer.post(
                f"/api/v1/performance/reviews/{review['id']}/feedback",
                json={"comment": PII_COMMENT},
            )
        ).status_code == 201

    async with client_factory(_manager()) as manager:
        audit_before = len(await _review_audit_rows(session))
        outbox_before = len(await _outbox_rows(session))
        mixed = await manager.patch(
            f"/api/v1/performance/reviews/{review['id']}",
            json={"rating": 2, "feedback_provider_ids": []},
        )
        removal = await manager.patch(
            f"/api/v1/performance/reviews/{review['id']}",
            json={"feedback_provider_ids": []},
        )
        add = await manager.patch(
            f"/api/v1/performance/reviews/{review['id']}",
            json={"feedback_provider_ids": [str(PEER_EMPLOYEE), str(ACTOR)]},
        )
        after = await manager.get(f"/api/v1/performance/reviews/{review['id']}")

    _assert_error(mixed, 409, "feedback_already_submitted")
    _assert_error(removal, 409, "feedback_already_submitted")
    assert add.status_code == 200
    # The refused mixed PATCH wrote no field change, audit row or event.
    assert after.status_code == 200
    assert after.json()["rating"] is None
    audit = await _review_audit_rows(session)
    assert len(audit) == audit_before + 1  # the successful provider add only
    assert audit[-1].action == "review.updated"
    assert len(await _outbox_rows(session)) == outbox_before
    assert set(add.json()["feedback_provider_ids"]) == {
        str(PEER_EMPLOYEE),
        str(ACTOR),
    }


async def test_review_visibility_scope_and_tenant_isolation(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(identity(role=Role.HR, user_id=HR_USER)) as hr:
        report_review = (await _create_review(hr, employee_id=REPORT_EMPLOYEE)).json()
        peer_review = (await _create_review(hr, employee_id=PEER_EMPLOYEE)).json()

    async with client_factory(_manager()) as manager:
        manager_list = await manager.get("/api/v1/performance/reviews")
        report_detail = await manager.get(
            f"/api/v1/performance/reviews/{report_review['id']}"
        )
        peer_detail = await manager.get(
            f"/api/v1/performance/reviews/{peer_review['id']}"
        )
        peer_filter = await manager.get(
            "/api/v1/performance/reviews",
            params={"employee_id": str(PEER_EMPLOYEE)},
        )
        patch_foreign_draft = await manager.patch(
            f"/api/v1/performance/reviews/{report_review['id']}",
            json={"rating": 3},
        )

    assert manager_list.status_code == 200
    assert [item["id"] for item in manager_list.json()["items"]] == [
        report_review["id"]
    ]
    assert report_detail.status_code == 200
    _assert_error(peer_detail, 404, "review_not_found")
    _assert_error(peer_filter, 404, "employee_not_found")
    # The manager can see their report's review but did not author it.
    _assert_error(patch_foreign_draft, 403, "performance_denied")

    async with client_factory(identity(role=Role.EMPLOYEE)) as employee:
        own_list = await employee.get("/api/v1/performance/reviews")
        foreign_detail = await employee.get(
            f"/api/v1/performance/reviews/{report_review['id']}"
        )
        foreign_filter = await employee.get(
            "/api/v1/performance/reviews",
            params={"employee_id": str(REPORT_EMPLOYEE)},
        )
    assert own_list.status_code == 200
    assert own_list.json()["items"] == []
    _assert_error(foreign_detail, 404, "review_not_found")
    _assert_error(foreign_filter, 404, "employee_not_found")

    async with client_factory(_hr()) as hr:
        hr_list = await hr.get("/api/v1/performance/reviews")
    assert {item["id"] for item in hr_list.json()["items"]} == {
        report_review["id"],
        peer_review["id"],
    }

    # Tenant B: a same-tenant subject makes nested provider ids the 404 path.
    async with client_factory(identity(tenant=TENANT_B, role=Role.ADMIN)) as tb_admin:
        tb_refs = await create_org_refs(tb_admin, suffix="-b")
    await _seed_employee(
        session,
        tb_refs,
        employee_id=TENANT_B_EMPLOYEE,
        code="EMP-00091",
        email="b@example.test",
        first_name="Bea",
        last_name="Bee",
        tenant_id=TENANT_B,
    )

    outer_hr = identity(
        tenant=TENANT_B, role=Role.HR, user_id=HR_USER, employee_id=None
    )
    async with client_factory(outer_hr) as tenant_b:
        cross_detail = await tenant_b.get(
            f"/api/v1/performance/reviews/{report_review['id']}"
        )
        cross_feedback = await tenant_b.get(
            f"/api/v1/performance/reviews/{report_review['id']}/feedback"
        )
        cross_filter = await tenant_b.get(
            "/api/v1/performance/reviews",
            params={"employee_id": str(REPORT_EMPLOYEE)},
        )
        cross_subject = await _create_review(tenant_b, employee_id=REPORT_EMPLOYEE)
        cross_provider = await _create_review(
            tenant_b,
            employee_id=TENANT_B_EMPLOYEE,
            providers=(PEER_EMPLOYEE,),
        )
        cross_patch = await tenant_b.patch(
            f"/api/v1/performance/reviews/{report_review['id']}",
            json={"rating": 3},
        )
        cross_complete = await tenant_b.post(
            f"/api/v1/performance/reviews/{report_review['id']}/complete"
        )
        cross_cancel = await tenant_b.post(
            f"/api/v1/performance/reviews/{report_review['id']}/cancel"
        )
        cross_submit = await tenant_b.post(
            f"/api/v1/performance/reviews/{report_review['id']}/feedback",
            json={"comment": "cross tenant"},
        )
    _assert_error(cross_detail, 404, "review_not_found")
    _assert_error(cross_feedback, 404, "review_not_found")
    _assert_error(cross_filter, 404, "employee_not_found")
    _assert_error(cross_subject, 404, "employee_not_found")
    _assert_error(cross_provider, 404, "employee_not_found")
    _assert_error(cross_patch, 404, "review_not_found")
    _assert_error(cross_complete, 404, "review_not_found")
    _assert_error(cross_cancel, 404, "review_not_found")
    _assert_error(cross_submit, 404, "review_not_found")


async def test_feedback_worklist_query_rules_and_pagination(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_manager()) as manager:
        first = (
            await _create_review(
                manager,
                employee_id=REPORT_EMPLOYEE,
                providers=(PEER_EMPLOYEE,),
            )
        ).json()
        second = (await _create_review(manager, employee_id=REPORT_EMPLOYEE)).json()
        third = (await _create_review(manager, employee_id=REPORT_EMPLOYEE)).json()
        clamped = await manager.get(
            "/api/v1/performance/reviews",
            params={"page": 0, "page_size": 1000},
        )

    assert clamped.status_code == 200
    assert clamped.json()["page"] == {
        "page": 1,
        "page_size": 100,
        "total_items": 3,
        "total_pages": 1,
    }
    assert {item["id"] for item in clamped.json()["items"]} == {
        first["id"],
        second["id"],
        third["id"],
    }

    async with client_factory(_peer()) as peer:
        worklist = await peer.get(
            "/api/v1/performance/reviews", params={"feedback_for_me": "true"}
        )
        draft_only = await peer.get(
            "/api/v1/performance/reviews",
            params={"feedback_for_me": "true", "status": "draft"},
        )
        conflict = await peer.get(
            "/api/v1/performance/reviews",
            params={"feedback_for_me": "true", "status": "completed"},
        )
        empty_sheet = await peer.get(
            "/api/v1/performance/reviews",
            params={"feedback_for_me": "true", "page": 0, "page_size": 1000},
        )
    assert worklist.status_code == 200
    [worklist_item] = worklist.json()["items"]
    assert worklist_item["id"] == first["id"]
    assert worklist_item["rating"] is None
    assert worklist_item["feedback_provider_ids"] is None
    assert draft_only.status_code == 200
    assert [item["id"] for item in draft_only.json()["items"]] == [first["id"]]
    _assert_error(conflict, 422, "validation_error")
    assert empty_sheet.status_code == 200
    assert empty_sheet.json()["page"] == {
        "page": 1,
        "page_size": 100,
        "total_items": 1,
        "total_pages": 1,
    }

    async with client_factory(_peer()) as peer:
        unknown_filter = await peer.get(
            "/api/v1/performance/reviews",
            params={
                "feedback_for_me": "true",
                "employee_id": str(UNKNOWN_EMPLOYEE),
            },
        )
    _assert_error(unknown_filter, 404, "employee_not_found")

    async with client_factory(_manager()) as manager:
        assert (await _rating_patch(manager, first["id"])).status_code == 200
        assert (
            await manager.post(f"/api/v1/performance/reviews/{first['id']}/complete")
        ).status_code == 200
    async with client_factory(_peer()) as peer:
        closed = await peer.get(
            "/api/v1/performance/reviews", params={"feedback_for_me": "true"}
        )
    assert closed.json()["items"] == []


async def test_cancelled_review_is_not_mutable_and_feedback_is_closed(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled review stays readable to its author but accepts no work."""
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_manager()) as manager:
        review = (
            await _create_review(
                manager,
                employee_id=REPORT_EMPLOYEE,
                providers=(PEER_EMPLOYEE,),
            )
        ).json()
        assert (
            await manager.post(f"/api/v1/performance/reviews/{review['id']}/cancel")
        ).status_code == 200
        patch_cancelled = await manager.patch(
            f"/api/v1/performance/reviews/{review['id']}", json={"rating": 4}
        )
        complete_cancelled = await manager.post(
            f"/api/v1/performance/reviews/{review['id']}/complete"
        )
        read_cancelled = await manager.get(
            f"/api/v1/performance/reviews/{review['id']}"
        )
        feedback_cancelled = await manager.get(
            f"/api/v1/performance/reviews/{review['id']}/feedback"
        )

    _assert_error(patch_cancelled, 409, "review_not_draft")
    _assert_error(complete_cancelled, 409, "review_not_draft")
    assert read_cancelled.status_code == 200
    assert read_cancelled.json()["status"] == ReviewStatus.CANCELLED.value
    assert feedback_cancelled.status_code == 200
    assert feedback_cancelled.json()["items"] == []

    async with client_factory(_peer()) as peer:
        late = await peer.post(
            f"/api/v1/performance/reviews/{review['id']}/feedback",
            json={"comment": "late"},
        )
    _assert_error(late, 409, "review_not_draft")

    async with client_factory(_subject()) as subject:
        hidden = await subject.get(f"/api/v1/performance/reviews/{review['id']}")
    _assert_error(hidden, 404, "review_not_found")

    # The cancel transition emitted exactly one event and one audit row.
    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "performance.review_started",
        "performance.review_cancelled",
    ]
    audit = await _review_audit_rows(session)
    assert [row.action for row in audit] == ["review.started", "review.cancelled"]
    assert audit[-1].changes == {"status": {"old": "draft", "new": "cancelled"}}
    assert USER not in {row.actor_id for row in audit}  # the manager acted


async def test_hr_editor_cannot_nominate_the_review_author(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`reviews.author_id` is a user id; the adapter resolves its employee link.

    The manager authored the review, HR edits it, and the nomination set must
    still exclude the original author's employee — not just the HR editor's.
    """
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    session.add(
        identity_orm.User(
            id=MANAGER_USER,
            tenant_id=TENANT_A,
            email="manager-user@example.test",
            role=Role.MANAGER.value,
            employee_id=MANAGER_EMPLOYEE,
            password_hash="not-used-by-this-test",
            active=True,
        )
    )
    await session.flush()

    async with client_factory(_manager()) as manager:
        review = (await _create_review(manager, employee_id=REPORT_EMPLOYEE)).json()

    async with client_factory(_hr()) as hr:
        nominate_author = await hr.patch(
            f"/api/v1/performance/reviews/{review['id']}",
            json={"feedback_provider_ids": [str(MANAGER_EMPLOYEE)]},
        )
        nominate_peer = await hr.patch(
            f"/api/v1/performance/reviews/{review['id']}",
            json={"feedback_provider_ids": [str(PEER_EMPLOYEE)]},
        )

    _assert_error(nominate_author, 409, "feedback_provider_invalid")
    assert nominate_peer.status_code == 200
    assert nominate_peer.json()["feedback_provider_ids"] == [str(PEER_EMPLOYEE)]


async def test_management_subject_cannot_list_own_draft(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A management-role subject gets the same draft invisibility as everyone."""
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)

    async with client_factory(_hr()) as hr:
        draft = (await _create_review(hr, employee_id=REPORT_EMPLOYEE)).json()

    hr_subject = identity(
        role=Role.HR, user_id=REPORT_USER, employee_id=REPORT_EMPLOYEE
    )
    async with client_factory(hr_subject) as subject_hr:
        listed = await subject_hr.get("/api/v1/performance/reviews")
        own_filter = await subject_hr.get(
            "/api/v1/performance/reviews",
            params={"employee_id": str(REPORT_EMPLOYEE)},
        )
        detail = await subject_hr.get(f"/api/v1/performance/reviews/{draft['id']}")

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert own_filter.status_code == 200
    assert own_filter.json()["items"] == []
    _assert_error(detail, 404, "review_not_found")

    async with client_factory(_hr()) as hr:
        assert (await _rating_patch(hr, draft["id"])).status_code == 200
        assert (
            await hr.post(f"/api/v1/performance/reviews/{draft['id']}/complete")
        ).status_code == 200

    async with client_factory(hr_subject) as subject_hr:
        listed_after = await subject_hr.get("/api/v1/performance/reviews")
        detail_after = await subject_hr.get(
            f"/api/v1/performance/reviews/{draft['id']}"
        )
    assert [item["id"] for item in listed_after.json()["items"]] == [draft["id"]]
    assert detail_after.status_code == 200
    assert detail_after.json()["feedback_provider_ids"] is None
