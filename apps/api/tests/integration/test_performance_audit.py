"""Cross-cutting audit and PII sweep for every performance mutation (T8).

One flow drives all seventeen mutating routes (goals, reviews/feedback, skill
catalog, employee skills). It then inspects the append-only audit rows, the
outbox envelopes and the captured log lines:

- exactly one `AuditEntry` exists per successful mutation (19 mutations — two
  goals are created and two reviews are started);
- every PII field appears in audit `changes` only as the `"changed"` marker —
  never as a value, and never as a numeric rating;
- no event payload carries a title, description, period, strength,
  improvement, comment, note or rating — events are ids + state only;
- no log line contains any of the concrete PII values.

This is the T8 sweep pinned by `.ai/features/performance.md` (acceptance
criterion 9). It complements the per-flow assertions in the goal, review and
skill integration suites.
"""

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
import structlog
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import performance as performance_api
from app.domains.employees.schemas import EmployeeStatus, EmploymentType
from app.infrastructure.postgres.models_audit import AuditEntry
from app.infrastructure.postgres.models_outbox import EventOutboxRow
from app.infrastructure.postgres.repositories.employees import (
    PostgresEmployeeRepository,
)
from app.shared.identity import Role
from tests.integration.conftest import (
    ACTOR,
    TENANT_A,
    ClientFactory,
    create_org_refs,
    identity,
)

CLOCK = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MANAGER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000021")
REPORT_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000022")
PEER_EMPLOYEE = UUID("00000000-0000-4000-8000-000000000023")

MANAGER_USER = UUID("00000000-0000-4000-8000-000000000041")
PEER_USER = UUID("00000000-0000-4000-8000-000000000043")
HR_USER = UUID("00000000-0000-4000-8000-000000000044")

PII_GOAL_TITLE = "Secret roadmap — pii-goal-title"
PII_GOAL_TITLE_2 = "Renamed secret roadmap — pii-goal-title-2"
PII_GOAL_TITLE_3 = "Second goal — pii-goal-title-3"
PII_GOAL_DESCRIPTION = "Private plan — pii-goal-description"
PII_PERIOD = "2026-H1 secret — pii-review-period"
PII_STRENGTHS = "Secret strength — pii-review-strengths"
PII_IMPROVEMENTS = "Secret improvement — pii-review-improvements"
PII_COMMENT = "Private peer comment — pii-feedback-comment"
PII_SKILL_DESCRIPTION = "Internal calibration notes — pii-skill-description"
PII_SKILL_DESCRIPTION_2 = "Updated calibration notes — pii-skill-description-2"
PII_NOTE = "Confidential proficiency note — pii-employee-skill-note"
PII_NOTE_2 = "Updated confidential note — pii-employee-skill-note-2"

PII_VALUES = (
    PII_GOAL_TITLE,
    PII_GOAL_TITLE_2,
    PII_GOAL_TITLE_3,
    PII_GOAL_DESCRIPTION,
    PII_PERIOD,
    PII_STRENGTHS,
    PII_IMPROVEMENTS,
    PII_COMMENT,
    PII_SKILL_DESCRIPTION,
    PII_SKILL_DESCRIPTION_2,
    PII_NOTE,
    PII_NOTE_2,
)

# Top-level audit `changes` keys that must only ever hold the marker.
PII_CHANGE_KEYS = frozenset(
    {
        "title",
        "description",
        "period",
        "rating",
        "strengths",
        "improvements",
        "comment",
        "note",
    }
)

RATING = 4
PROFICIENCY = 3
PROFICIENCY_UPDATED = 4


def _hr() -> Any:
    return identity(role=Role.HR, user_id=HR_USER, employee_id=None)


def _manager() -> Any:
    return identity(
        role=Role.MANAGER, user_id=MANAGER_USER, employee_id=MANAGER_EMPLOYEE
    )


def _employee() -> Any:
    return identity(role=Role.EMPLOYEE, employee_id=ACTOR)


def _peer() -> Any:
    return identity(role=Role.EMPLOYEE, user_id=PEER_USER, employee_id=PEER_EMPLOYEE)


def _assert_status(response: Response, status: int) -> None:
    assert response.status_code == status, response.text


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
) -> None:
    await PostgresEmployeeRepository(session).add(
        TENANT_A,
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


async def _seed_world(client_factory: ClientFactory, session: AsyncSession) -> None:
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


PERFORMANCE_ENTITY_TYPES = ("goal", "review", "feedback", "skill", "employee_skill")


async def _audit_rows(session: AsyncSession) -> list[AuditEntry]:
    result = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type.in_(PERFORMANCE_ENTITY_TYPES))
        .order_by(AuditEntry.id)
    )
    return list(result.scalars().all())


async def _outbox_rows(session: AsyncSession) -> list[EventOutboxRow]:
    result = await session.execute(select(EventOutboxRow).order_by(EventOutboxRow.id))
    return list(result.scalars().all())


def _is_audit_value(value: Any) -> bool:
    """Ids, state, timestamps or lists of ids — never free text."""
    if isinstance(value, dict):
        return set(value) == {"old", "new"} and all(
            _is_audit_value(nested) for nested in value.values()
        )
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    return value is None or isinstance(value, (str, int, float, bool))


def _assert_pii_markers_only(changes: dict[str, Any]) -> None:
    """PII keys carry the `"changed"` marker; everything else is id/state."""
    for key, value in changes.items():
        if key in PII_CHANGE_KEYS:
            assert value == "changed", f"{key} leaked its value: {value!r}"
        else:
            assert _is_audit_value(value)


async def test_every_performance_mutation_is_audited_without_pii(
    client_factory: ClientFactory,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_api, "_now", lambda: CLOCK)
    await _seed_world(client_factory, session)
    structlog.configure(cache_logger_on_first_use=False)

    with structlog.testing.capture_logs() as logs:
        # --- goals: create, update, activate, complete; create, cancel ------
        async with client_factory(_employee()) as employee:
            goal = await employee.post(
                "/api/v1/performance/goals",
                json={
                    "title": PII_GOAL_TITLE,
                    "description": PII_GOAL_DESCRIPTION,
                    "due_date": "2026-11-01",
                    "progress_pct": 25,
                },
            )
            _assert_status(goal, 201)
            goal_id = goal.json()["id"]
            _assert_status(
                await employee.patch(
                    f"/api/v1/performance/goals/{goal_id}",
                    json={"title": PII_GOAL_TITLE_2, "progress_pct": 60},
                ),
                200,
            )
            _assert_status(
                await employee.post(f"/api/v1/performance/goals/{goal_id}/activate"),
                200,
            )
            _assert_status(
                await employee.post(f"/api/v1/performance/goals/{goal_id}/complete"),
                200,
            )
            second_goal = await employee.post(
                "/api/v1/performance/goals",
                json={"title": PII_GOAL_TITLE_3},
            )
            _assert_status(second_goal, 201)
            _assert_status(
                await employee.post(
                    f"/api/v1/performance/goals/{second_goal.json()['id']}/cancel"
                ),
                200,
            )

        # --- reviews: create, update, complete; create, cancel; feedback ----
        async with client_factory(_manager()) as manager:
            review = await manager.post(
                "/api/v1/performance/reviews",
                json={
                    "employee_id": str(REPORT_EMPLOYEE),
                    "period": PII_PERIOD,
                    "feedback_provider_ids": [str(PEER_EMPLOYEE)],
                },
            )
            _assert_status(review, 201)
            review_id = review.json()["id"]
            _assert_status(
                await manager.patch(
                    f"/api/v1/performance/reviews/{review_id}",
                    json={
                        "rating": RATING,
                        "strengths": PII_STRENGTHS,
                        "improvements": PII_IMPROVEMENTS,
                    },
                ),
                200,
            )
        # A fresh client per identity: the harness overrides the session
        # identity globally, so identities are not nested.
        async with client_factory(_peer()) as peer:
            _assert_status(
                await peer.post(
                    f"/api/v1/performance/reviews/{review_id}/feedback",
                    json={"comment": PII_COMMENT},
                ),
                201,
            )

        async with client_factory(_manager()) as manager:
            _assert_status(
                await manager.post(f"/api/v1/performance/reviews/{review_id}/complete"),
                200,
            )
            second_review = await manager.post(
                "/api/v1/performance/reviews",
                json={"employee_id": str(REPORT_EMPLOYEE)},
            )
            _assert_status(second_review, 201)
            _assert_status(
                await manager.post(
                    f"/api/v1/performance/reviews/{second_review.json()['id']}/cancel"
                ),
                200,
            )

        # --- skills: catalog + employee skill rows --------------------------
        async with client_factory(_hr()) as hr:
            skill = await hr.post(
                "/api/v1/performance/skills",
                json={
                    "name": "Python",
                    "description": PII_SKILL_DESCRIPTION,
                },
            )
            _assert_status(skill, 201)
            skill_id = skill.json()["id"]
            _assert_status(
                await hr.patch(
                    f"/api/v1/performance/skills/{skill_id}",
                    json={
                        "name": "Python 3",
                        "description": PII_SKILL_DESCRIPTION_2,
                    },
                ),
                200,
            )
            _assert_status(
                await hr.post(f"/api/v1/performance/skills/{skill_id}/archive"),
                200,
            )
            _assert_status(
                await hr.post(f"/api/v1/performance/skills/{skill_id}/restore"),
                200,
            )
            _assert_status(
                await hr.post(
                    f"/api/v1/performance/employees/{ACTOR}/skills",
                    json={
                        "skill_id": skill_id,
                        "proficiency": PROFICIENCY,
                        "note": PII_NOTE,
                    },
                ),
                201,
            )
            _assert_status(
                await hr.patch(
                    f"/api/v1/performance/employees/{ACTOR}/skills/{skill_id}",
                    json={
                        "proficiency": PROFICIENCY_UPDATED,
                        "note": PII_NOTE_2,
                    },
                ),
                200,
            )
            _assert_status(
                await hr.delete(
                    f"/api/v1/performance/employees/{ACTOR}/skills/{skill_id}"
                ),
                204,
            )

    # --- exactly one audit entry per successful mutation ---------------------
    audit = await _audit_rows(session)
    assert [row.action for row in audit] == [
        "goal.created",
        "goal.updated",
        "goal.activated",
        "goal.completed",
        "goal.created",
        "goal.cancelled",
        "review.started",
        "review.updated",
        "feedback.submitted",
        "review.completed",
        "review.started",
        "review.cancelled",
        "skill.created",
        "skill.updated",
        "skill.archived",
        "skill.restored",
        "employee_skill.recorded",
        "employee_skill.updated",
        "employee_skill.removed",
    ]
    assert len(audit) == 19
    assert all(row.changes is not None for row in audit)
    for row in audit:
        _assert_pii_markers_only(row.changes or {})

    audit_dump = json.dumps([row.changes for row in audit], default=str)
    for value in PII_VALUES:
        assert value not in audit_dump

    # A numeric rating is not hiding under a non-PII key: only the marker is
    # ever recorded for the review's evaluative fields.
    for row in audit:
        for key, value in (row.changes or {}).items():
            if key == "rating":
                assert value == "changed"
            assert value != RATING

    # --- outbox: events are ids + state only --------------------------------
    outbox = await _outbox_rows(session)
    assert [row.event_type for row in outbox] == [
        "performance.goal_created",
        "performance.goal_activated",
        "performance.goal_completed",
        "performance.goal_created",
        "performance.goal_cancelled",
        "performance.review_started",
        "performance.feedback_submitted",
        "performance.review_completed",
        "performance.review_started",
        "performance.review_cancelled",
    ]
    outbox_dump = json.dumps([row.payload for row in outbox], default=str)
    for value in PII_VALUES:
        assert value not in outbox_dump
    for row in outbox:
        data = row.payload["data"]
        assert "employee_id" in data
        assert PII_CHANGE_KEYS.isdisjoint(data)
        if "status" in data:
            assert data["status"] in {"draft", "active", "completed", "cancelled"}

    # --- logs: no concrete PII value ever reaches a log line ----------------
    assert logs, "expected the request middleware to log each call"
    log_dump = json.dumps(logs, default=str)
    for value in PII_VALUES:
        assert value not in log_dump
    assert f'"rating": {RATING}' not in log_dump
