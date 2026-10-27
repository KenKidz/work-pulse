"""Use cases for the Performance domain — goals (T5), reviews (T6), skills (T7).

Rules implemented here (pinned in `.ai/features/performance.md`):

- **Goal lifecycle**: `(create) → DRAFT → ACTIVE → COMPLETED`, with
  `CANCELLED` reachable from `DRAFT`/`ACTIVE`. `COMPLETED`/`CANCELLED` are
  terminal: `activate` only from `draft` (`409 goal_not_draft`), `complete` only
  from `active` (`409 goal_not_active`), `update`/`cancel` on a terminal row are
  `409 goal_terminal`. Every transition is one conditional repository update
  plus one event envelope and one audit entry in the same transaction (ADR
  0012); `None` from the conditional update is a lost race and maps to the
  same pinned wrong-state error, never a silent overwrite.
- **Review lifecycle** (T6): `(create) → DRAFT → COMPLETED | CANCELLED`, both
  terminal. `complete` needs a rating (`409 review_incomplete`); a completed
  review is immutable (`409 review_not_draft`). Creation is Manager (self +
  direct reports) or HR/Admin/Super Admin, never self (`403`). The subject must
  be an active employee (`409 employee_not_active`); foreign subjects and
  nested ids are `404`.
- **Review visibility**: the subject sees only completed reviews (a draft id is
  `404`, never a denied leak) without the nomination list; the author sees
  their reviews in every state; a manager sees direct reports in every state and
  their own subject rows only when completed; HR/Admin/Super Admin see all. A
  nominated provider with no other standing sees the shell (id, status) of the
  draft needed to file feedback, never its rating or text.
- **Feedback**: providers are nominated on a draft by the author or HR/Admin and
  are active employees other than the subject/author (`409
  feedback_provider_invalid`). One immutable comment per provider per review
  (`409 feedback_already_submitted`), only while `draft` (`409
  review_not_draft`), emitted as `performance.feedback_submitted`. The author
  and HR/Admin see every comment; a provider sees only their own; the subject
  is always refused (`403 feedback_access_denied`).
- **Goals authorship and scope**: the subject may manage their own goals; a
  Manager manages self + direct reports through the shared `EmployeeScope` port;
  HR/Admin/Super Admin manage anyone. Out-of-scope and foreign subjects are
  `404 employee_not_found` (never `403`); Finance has no management standing
  (`403 performance_denied`). `GoalCreate.employee_id` omitted defaults to the
  actor's employee link; a missing link fails closed with `403`
  `performance_denied` (overtime's missing-link precedent).
- **Progress is a field update**, not a transition: it emits no event but is
  audited (`goal.updated`). Title, description and progress are editable only
  while `draft|active`; terminal goals never change again. Completed never
  auto-forwards `progress_pct`. Review field edits likewise emit no event but
  are audited (`review.updated`).
- **PII** (`.ai/security.md`): goal `title`/`description`, review `period`/
  `rating`/`strengths`/`improvements`, feedback `comment` and employee-skill
  `note` are never logged, emitted or written into audit `changes` values — the
  audit records the `"changed"` marker only. Events carry ids and state only.
- **Skills** (T7): the tenant catalog follows ADR 0007 soft-delete semantics —
  `archived_at`, no DELETE route, archive/restore are no-ops when the row is
  already in the target state (no audit entry), and name uniqueness spans
  archived rows (`409 skill_name_taken`). The one deliberate divergence:
  archiving a skill in use is allowed; only new assignment is blocked
  (`409 skill_archived`) and existing employee rows stay editable. Employee
  skills attach one catalog skill per `(employee, skill)` (`409
  employee_skill_exists`), are keyed by `skill_id`, and are audited on record/
  update/remove — never evented (no workflow state).
- **Clock**: every mutation takes `now` as its last parameter — the router is
  the only wall-clock reader.
- Tenant isolation is the repository's (`WHERE tenant_id = …` first); this
  service never trusts a client-supplied id across tenants.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from uuid_utils.compat import uuid7

from app.domains.performance import events
from app.domains.performance.exceptions import (
    EmployeeNotActiveError,
    EmployeeNotFoundError,
    EmployeeSkillExistsError,
    FeedbackAlreadySubmittedError,
    FeedbackProviderInvalidError,
    GoalNotActiveError,
    GoalNotDraftError,
    GoalNotFoundError,
    GoalTerminalError,
    PerformanceDeniedError,
    PerformanceValidationError,
    ReviewIncompleteError,
    ReviewNotDraftError,
    ReviewNotFoundError,
    SkillArchivedError,
    SkillNameTakenError,
    SkillNotFoundError,
)
from app.domains.performance.policies import (
    ensure_can_manage_goals,
    ensure_can_manage_skills,
    ensure_can_nominate,
    ensure_can_record_skills,
    ensure_can_run_reviews,
    ensure_can_view_feedback,
    is_management,
)
from app.domains.performance.repository import (
    EmployeeScope,
    GoalFilters,
    PerformanceRepository,
    ReviewFilters,
    SkillFilters,
)
from app.domains.performance.schemas import (
    EmployeeSkill,
    EmployeeSkillCreate,
    EmployeeSkillList,
    EmployeeSkillUpdate,
    Goal,
    GoalCreate,
    GoalListPage,
    GoalStatus,
    GoalUpdate,
    Review,
    ReviewCreate,
    ReviewFeedback,
    ReviewFeedbackCreate,
    ReviewFeedbackList,
    ReviewListPage,
    ReviewStatus,
    ReviewUpdate,
    Skill,
    SkillCreate,
    SkillListPage,
    SkillUpdate,
)
from app.shared.audit import AuditEntryCreate, AuditWriter
from app.shared.identity import Identity, Role
from app.shared.outbox import EventOutbox
from app.shared.pagination import PageParams

PII_CHANGED = "changed"  # audit marker: field changed, value deliberately absent
_TERMINAL_GOAL_STATUSES = (GoalStatus.COMPLETED, GoalStatus.CANCELLED)
_GOAL_PII_FIELDS = frozenset({"title", "description"})


def _value(value: object) -> object:
    """Audit-safe JSON value: ids/dates/enums become strings, never objects."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, GoalStatus):
        return value.value
    return value


def _without_providers(review: Review) -> Review:
    """Subject-facing serialization: the nomination list is not theirs to see."""
    return review.model_copy(update={"feedback_provider_ids": None})


def _review_shell(review: Review) -> Review:
    """Provider-only shell: enough to file feedback, never the draft's text."""
    return Review(
        id=review.id,
        employee_id=review.employee_id,
        period=None,
        rating=None,
        strengths=None,
        improvements=None,
        status=review.status,
        feedback_provider_ids=None,
        created_at=review.created_at,
        updated_at=review.updated_at,
        completed_at=review.completed_at,
    )


class GoalService:
    def __init__(
        self,
        repo: PerformanceRepository,
        scope: EmployeeScope,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._scope = scope
        self._audit = audit
        self._outbox = outbox

    # --- mutations -----------------------------------------------------------

    async def create_goal(
        self, identity: Identity, payload: GoalCreate, now: datetime
    ) -> Goal:
        """Create a goal in `draft` for the payload subject (default: self)."""
        ensure_can_manage_goals(identity)
        subject_id = (
            payload.employee_id
            if payload.employee_id is not None
            else identity.employee_id
        )
        if subject_id is None:
            # No employee link and no explicit subject: fail closed (overtime's
            # missing-link precedent), never invent a subject.
            raise PerformanceDeniedError()
        await self._ensure_active_subject(identity, subject_id)

        goal = await self._repo.create_goal(
            identity.tenant_id,
            uuid7(),
            employee_id=subject_id,
            title=payload.title,
            description=payload.description,
            due_date=payload.due_date,
            progress_pct=payload.progress_pct,
        )

        await self._outbox.append(
            events.goal_created(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                goal_id=goal.id,
                employee_id=goal.employee_id,
            )
        )
        changes: dict[str, Any] = {
            "employee_id": {"old": None, "new": str(goal.employee_id)},
            "status": {"old": None, "new": goal.status.value},
            "progress_pct": {"old": None, "new": goal.progress_pct},
            "title": PII_CHANGED,  # PII: never the value
        }
        if goal.due_date is not None:
            changes["due_date"] = {"old": None, "new": goal.due_date.isoformat()}
        if goal.description is not None:
            changes["description"] = PII_CHANGED  # PII: never the value
        await self._write_audit(identity, "goal.created", goal.id, now, changes)
        return goal

    async def update_goal(
        self,
        identity: Identity,
        goal_id: UUID,
        payload: GoalUpdate,
        now: datetime,
    ) -> Goal:
        """Edit a `draft`/`active` goal; a terminal goal is immutable."""
        ensure_can_manage_goals(identity)
        existing = await self._manageable_goal(identity, goal_id)
        if existing.status in _TERMINAL_GOAL_STATUSES:
            raise GoalTerminalError()

        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            # A no-op edit changes no fact: no conditional write, no audit.
            return existing

        updated = await self._repo.update_goal(
            identity.tenant_id, goal_id, now=now, **changes
        )
        if updated is None:
            # The goal became terminal between the read and the conditional
            # update — never overwrite a completed/cancelled goal.
            raise GoalTerminalError()

        audit_changes: dict[str, Any] = {}
        for field in changes:
            if field in _GOAL_PII_FIELDS:
                audit_changes[field] = PII_CHANGED  # PII: never the value
            else:
                audit_changes[field] = {
                    "old": _value(getattr(existing, field)),
                    "new": _value(getattr(updated, field)),
                }
        await self._write_audit(identity, "goal.updated", goal_id, now, audit_changes)
        return updated

    async def activate_goal(
        self, identity: Identity, goal_id: UUID, now: datetime
    ) -> Goal:
        """`draft → active`; any other state is `409 goal_not_draft`."""
        ensure_can_manage_goals(identity)
        existing = await self._manageable_goal(identity, goal_id)
        if existing.status is not GoalStatus.DRAFT:
            raise GoalNotDraftError()

        updated = await self._repo.transition_goal_activate(
            identity.tenant_id, goal_id, now=now
        )
        if updated is None:
            # A concurrent transition already left `draft` — never double-apply.
            raise GoalNotDraftError()

        await self._outbox.append(
            events.goal_activated(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                goal_id=updated.id,
                employee_id=updated.employee_id,
            )
        )
        await self._write_audit(
            identity,
            "goal.activated",
            updated.id,
            now,
            {
                "status": {
                    "old": existing.status.value,
                    "new": updated.status.value,
                }
            },
        )
        return updated

    async def complete_goal(
        self, identity: Identity, goal_id: UUID, now: datetime
    ) -> Goal:
        """`active → completed`, stamping `completed_at`; progress is unchanged."""
        ensure_can_manage_goals(identity)
        existing = await self._manageable_goal(identity, goal_id)
        if existing.status is not GoalStatus.ACTIVE:
            raise GoalNotActiveError()

        updated = await self._repo.transition_goal_complete(
            identity.tenant_id, goal_id, now=now
        )
        if updated is None:
            # A concurrent transition already left `active`.
            raise GoalNotActiveError()

        await self._outbox.append(
            events.goal_completed(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                goal_id=updated.id,
                employee_id=updated.employee_id,
            )
        )
        await self._write_audit(
            identity,
            "goal.completed",
            updated.id,
            now,
            {
                "status": {
                    "old": existing.status.value,
                    "new": updated.status.value,
                }
            },
        )
        return updated

    async def cancel_goal(
        self, identity: Identity, goal_id: UUID, now: datetime
    ) -> Goal:
        """`draft|active → cancelled`, terminal; a terminal row is immutable."""
        ensure_can_manage_goals(identity)
        existing = await self._manageable_goal(identity, goal_id)
        if existing.status in _TERMINAL_GOAL_STATUSES:
            raise GoalTerminalError()

        updated = await self._repo.transition_goal_cancel(
            identity.tenant_id,
            goal_id,
            from_status=existing.status,
            now=now,
        )
        if updated is None:
            # A concurrent transition already made the goal terminal.
            raise GoalTerminalError()

        await self._outbox.append(
            events.goal_cancelled(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                goal_id=updated.id,
                employee_id=updated.employee_id,
            )
        )
        await self._write_audit(
            identity,
            "goal.cancelled",
            updated.id,
            now,
            {
                "status": {
                    "old": existing.status.value,
                    "new": updated.status.value,
                }
            },
        )
        return updated

    # --- reads ---------------------------------------------------------------

    async def list_goals(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None = None,
        status: GoalStatus | None = None,
        page: PageParams,
    ) -> GoalListPage:
        """Paged goals scoped to the caller's visibility (## Roles & access)."""
        visible = await self._list_scope(identity, employee_id)
        filters = GoalFilters(
            employee_id=employee_id,
            status=status,
            visible_employee_ids=visible,
        )
        items, total = await self._repo.list_goals(identity.tenant_id, filters, page)
        return GoalListPage(items=list(items), page=page.make_page(total))

    async def get_goal(self, identity: Identity, goal_id: UUID) -> Goal:
        """Goal detail; unknown, cross-tenant and out-of-scope are all `404`."""
        goal = await self._repo.get_goal(identity.tenant_id, goal_id)
        if goal is None or not await self._visible_subject(identity, goal.employee_id):
            raise GoalNotFoundError()
        return goal

    # --- internals -----------------------------------------------------------

    async def _ensure_active_subject(
        self, identity: Identity, employee_id: UUID
    ) -> None:
        """Validate the subject: exists in the tenant, visible, and active."""
        facts = await self._repo.employee_facts(identity.tenant_id, employee_id)
        if facts is None:
            raise EmployeeNotFoundError()
        if not await self._visible_subject(identity, employee_id):
            # Existence is not leaked to a caller without standing (404).
            raise EmployeeNotFoundError()
        if not facts.active:
            raise EmployeeNotActiveError()

    async def _manageable_goal(self, identity: Identity, goal_id: UUID) -> Goal:
        """Fetch a goal the caller may mutate; out-of-scope is a `404`."""
        goal = await self._repo.get_goal(identity.tenant_id, goal_id)
        if goal is None or not await self._visible_subject(identity, goal.employee_id):
            raise GoalNotFoundError()
        return goal

    async def _visible_subject(self, identity: Identity, employee_id: UUID) -> bool:
        """Whether the identity may see/manage this employee's goals."""
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            return (
                identity.employee_id is not None and employee_id == identity.employee_id
            )
        if identity.role is Role.MANAGER:
            if identity.employee_id is None:
                return False
            visible = await self._scope.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
            return employee_id in visible
        return True  # hr / admin / super_admin

    async def _list_scope(
        self, identity: Identity, employee_id: UUID | None
    ) -> Sequence[UUID] | None:
        """Effective subject scope; `None` means unscoped (HR/Admin/Super Admin).

        An explicit `employee_id` outside the caller's visibility, or unknown to
        the tenant, is a `404 employee_not_found` — existence is not leaked.
        """
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            if identity.employee_id is None:
                if employee_id is not None:
                    raise EmployeeNotFoundError()
                return []
            if employee_id is not None and employee_id != identity.employee_id:
                raise EmployeeNotFoundError()
            return [identity.employee_id]
        if identity.role is Role.MANAGER:
            visible = await self._scope.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
            if employee_id is not None:
                if employee_id not in visible:
                    raise EmployeeNotFoundError()
                return [employee_id]
            return visible
        if employee_id is not None:
            # Tenant-wide viewer: the filter target must still exist in this
            # tenant (pinned query semantics).
            if await self._repo.employee_facts(identity.tenant_id, employee_id) is None:
                raise EmployeeNotFoundError()
            return [employee_id]
        return None

    async def _write_audit(
        self,
        identity: Identity,
        action: str,
        goal_id: UUID,
        now: datetime,
        changes: dict[str, Any],
    ) -> None:
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action=action,
                entity_type="goal",
                entity_id=goal_id,
                occurred_at=now,
                changes=changes,
            )
        )


class ReviewService:
    """Review and feedback use cases (T6).

    Mutations follow the GoalService shape: a policy gate, a conditional
    repository write, then the event envelope and exactly one audit entry in the
    same transaction. Reads resolve visibility per row before any serialization
    so a draft review about the caller, or another department's review, is a
    `404` rather than a denied leak.
    """

    def __init__(
        self,
        repo: PerformanceRepository,
        scope: EmployeeScope,
        audit: AuditWriter,
        outbox: EventOutbox,
    ) -> None:
        self._repo = repo
        self._scope = scope
        self._audit = audit
        self._outbox = outbox

    # --- mutations -----------------------------------------------------------

    async def create_review(
        self, identity: Identity, payload: ReviewCreate, now: datetime
    ) -> Review:
        """Start a `draft` review; the actor is its author (never the subject)."""
        ensure_can_run_reviews(identity, payload.employee_id)
        await self._ensure_reviewable_subject(identity, payload.employee_id)
        provider_ids = await self._validate_providers(
            identity,
            subject_id=payload.employee_id,
            author_employee_id=identity.employee_id,
            provider_ids=payload.feedback_provider_ids,
        )

        review = await self._repo.create_review(
            identity.tenant_id,
            uuid7(),
            employee_id=payload.employee_id,
            author_id=identity.actor_id,
            period=payload.period,
            rating=None,
            strengths=None,
            improvements=None,
            feedback_provider_ids=provider_ids,
        )

        await self._outbox.append(
            events.review_started(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                review_id=review.id,
                employee_id=review.employee_id,
            )
        )
        changes: dict[str, Any] = {
            "employee_id": {"old": None, "new": str(review.employee_id)},
            "status": {"old": None, "new": review.status.value},
            "feedback_provider_ids": {
                "old": [],
                "new": [str(provider_id) for provider_id in provider_ids],
            },
        }
        if review.period is not None:
            changes["period"] = PII_CHANGED  # PII: never the value
        await self._write_audit(
            identity, "review.started", "review", review.id, now, changes
        )
        return review

    async def update_review(
        self,
        identity: Identity,
        review_id: UUID,
        payload: ReviewUpdate,
        now: datetime,
    ) -> Review:
        """Edit a `draft` review and/or its nomination set; terminal is `409`."""
        ensure_can_run_reviews(identity)
        existing, author_id = await self._manageable_review(identity, review_id)
        if existing.status is not ReviewStatus.DRAFT:
            raise ReviewNotDraftError()

        changes = payload.model_dump(exclude_unset=True)
        provider_ids = changes.pop("feedback_provider_ids", None)
        wanted: list[UUID] | None = None
        if provider_ids is not None:
            author_employee_id = await self._repo.user_employee_id(
                identity.tenant_id, author_id
            )
            wanted = await self._validate_providers(
                identity,
                subject_id=existing.employee_id,
                author_employee_id=author_employee_id,
                provider_ids=provider_ids,
            )

        provider_changed = wanted is not None and set(wanted) != set(
            existing.feedback_provider_ids or []
        )
        if not changes and not provider_changed:
            # A no-op edit changes no fact: no write, no audit.
            return existing

        removed = set(existing.feedback_provider_ids or []) - set(wanted or [])
        if provider_changed and removed:
            filed = set(
                await self._repo.feedback_author_ids(identity.tenant_id, review_id)
            )
            if removed & filed:
                # A provider who already filed keeps their standing (pinned
                # `409 feedback_already_submitted`); the repository re-checks
                # under the review lock as the race backstop. Checked before any
                # field write so the refusal leaves no partial change.
                raise FeedbackAlreadySubmittedError()

        updated = existing
        if changes:
            row = await self._repo.update_review(
                identity.tenant_id, review_id, now=now, **changes
            )
            if row is None:
                # The review left `draft` between the read and the write.
                raise ReviewNotDraftError()
            updated = row

        if provider_changed and wanted is not None:
            replaced = await self._repo.replace_review_providers(
                identity.tenant_id, review_id, employee_ids=wanted, now=now
            )
            if not replaced:
                raise ReviewNotDraftError()
            refreshed = await self._repo.get_review(identity.tenant_id, review_id)
            if refreshed is not None:
                updated = refreshed

        # Every ReviewUpdate metadata field is PII: markers only, no values.
        audit_changes: dict[str, Any] = dict.fromkeys(changes, PII_CHANGED)
        if provider_changed:
            audit_changes["feedback_provider_ids"] = {
                "old": [
                    str(provider_id)
                    for provider_id in existing.feedback_provider_ids or []
                ],
                "new": [
                    str(provider_id)
                    for provider_id in updated.feedback_provider_ids or []
                ],
            }
        await self._write_audit(
            identity, "review.updated", "review", review_id, now, audit_changes
        )
        return updated

    async def complete_review(
        self, identity: Identity, review_id: UUID, now: datetime
    ) -> Review:
        """`draft → completed`; needs a rating, stamps `completed_at`."""
        ensure_can_run_reviews(identity)
        existing, _ = await self._manageable_review(identity, review_id)
        if existing.status is not ReviewStatus.DRAFT:
            raise ReviewNotDraftError()
        if existing.rating is None:
            raise ReviewIncompleteError()

        updated = await self._repo.transition_review_complete(
            identity.tenant_id, review_id, now=now
        )
        if updated is None:
            # A concurrent editor won: report the state the row is actually in.
            current = await self._repo.get_review(identity.tenant_id, review_id)
            if (
                current is not None
                and current.status is ReviewStatus.DRAFT
                and current.rating is None
            ):
                raise ReviewIncompleteError()
            raise ReviewNotDraftError()

        await self._outbox.append(
            events.review_completed(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                review_id=updated.id,
                employee_id=updated.employee_id,
            )
        )
        await self._write_audit(
            identity,
            "review.completed",
            "review",
            updated.id,
            now,
            {
                "status": {
                    "old": existing.status.value,
                    "new": updated.status.value,
                }
            },
        )
        return updated

    async def cancel_review(
        self, identity: Identity, review_id: UUID, now: datetime
    ) -> Review:
        """`draft → cancelled`, terminal; a non-draft row is `409`."""
        ensure_can_run_reviews(identity)
        existing, _ = await self._manageable_review(identity, review_id)
        if existing.status is not ReviewStatus.DRAFT:
            raise ReviewNotDraftError()

        updated = await self._repo.transition_review_cancel(
            identity.tenant_id, review_id, now=now
        )
        if updated is None:
            raise ReviewNotDraftError()

        await self._outbox.append(
            events.review_cancelled(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                review_id=updated.id,
                employee_id=updated.employee_id,
            )
        )
        await self._write_audit(
            identity,
            "review.cancelled",
            "review",
            updated.id,
            now,
            {
                "status": {
                    "old": existing.status.value,
                    "new": updated.status.value,
                }
            },
        )
        return updated

    async def submit_feedback(
        self,
        identity: Identity,
        review_id: UUID,
        payload: ReviewFeedbackCreate,
        now: datetime,
    ) -> ReviewFeedback:
        """A nominated provider files their one immutable comment on a draft."""
        record = await self._repo.get_review_with_author(identity.tenant_id, review_id)
        if record is None:
            raise ReviewNotFoundError()
        review = record.review
        if identity.actor_id == record.author_id:
            # The author decides the review; they are never a peer provider,
            # even if a stale nomination somehow slipped through.
            raise PerformanceDeniedError()

        provider_id = identity.employee_id
        if (
            provider_id is None
            or provider_id == review.employee_id
            or provider_id not in (review.feedback_provider_ids or [])
        ):
            # Not nominated (the subject is never nominated by construction):
            # the pinned `403`, never an existence or content leak.
            raise PerformanceDeniedError()
        if review.status is not ReviewStatus.DRAFT:
            raise ReviewNotDraftError()
        if provider_id in set(
            await self._repo.feedback_author_ids(identity.tenant_id, review_id)
        ):
            raise FeedbackAlreadySubmittedError()

        feedback = await self._repo.create_feedback(
            identity.tenant_id,
            uuid7(),
            review_id=review_id,
            author_id=provider_id,
            comment=payload.comment,
            now=now,
        )
        if feedback is None:
            # The review completed/cancelled between the read and the lock.
            raise ReviewNotDraftError()

        await self._outbox.append(
            events.feedback_submitted(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                occurred_at=now,
                feedback_id=feedback.id,
                review_id=review_id,
                employee_id=review.employee_id,
            )
        )
        await self._write_audit(
            identity,
            "feedback.submitted",
            "feedback",
            feedback.id,
            now,
            {
                "review_id": {"old": None, "new": str(review_id)},
                "author_id": {"old": None, "new": str(provider_id)},
                "comment": PII_CHANGED,  # PII: never the value
            },
        )
        return feedback

    # --- reads ---------------------------------------------------------------

    async def get_review(self, identity: Identity, review_id: UUID) -> Review:
        """Review detail through the per-row visibility matrix."""
        record = await self._repo.get_review_with_author(identity.tenant_id, review_id)
        if record is None:
            raise ReviewNotFoundError()
        return await self._serialize_review(identity, record.review, record.author_id)

    async def list_reviews(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None = None,
        status: ReviewStatus | None = None,
        feedback_for_me: bool = False,
        page: PageParams,
    ) -> ReviewListPage:
        """Paged reviews scoped to the caller's visibility (## Roles & access)."""
        if feedback_for_me:
            return await self._list_feedback_worklist(
                identity, employee_id=employee_id, status=status, page=page
            )

        (
            visible,
            completed_only,
            hidden_non_completed,
            author_id,
            narrowed,
        ) = await self._list_scope(identity, employee_id)
        filters = ReviewFilters(
            employee_id=employee_id,
            status=narrowed if narrowed is not None else status,
            visible_employee_ids=visible,
            completed_only_employee_ids=completed_only,
            hidden_non_completed_employee_ids=hidden_non_completed,
            author_id=author_id,
        )
        items, total = await self._repo.list_reviews(identity.tenant_id, filters, page)
        return ReviewListPage(
            items=self._serialize_rows(identity, items, provider_worklist=False),
            page=page.make_page(total),
        )

    async def list_review_feedback(
        self, identity: Identity, review_id: UUID
    ) -> ReviewFeedbackList:
        """Feedback the caller may see: all (author/HR) or their own (provider)."""
        record = await self._repo.get_review_with_author(identity.tenant_id, review_id)
        if record is None:
            raise ReviewNotFoundError()
        review = record.review
        is_author = record.author_id == identity.actor_id
        is_provider = identity.employee_id is not None and identity.employee_id in (
            review.feedback_provider_ids or []
        )
        is_subject = (
            identity.employee_id is not None
            and identity.employee_id == review.employee_id
        )
        ensure_can_view_feedback(
            identity,
            is_author=is_author,
            is_provider=is_provider,
            is_subject=is_subject,
        )

        only_author = (
            None if is_management(identity) or is_author else identity.employee_id
        )
        items = await self._repo.list_feedback(
            identity.tenant_id, review_id, author_id=only_author
        )
        return ReviewFeedbackList(items=list(items))

    # --- list internals ------------------------------------------------------

    async def _list_feedback_worklist(
        self,
        identity: Identity,
        *,
        employee_id: UUID | None,
        status: ReviewStatus | None,
        page: PageParams,
    ) -> ReviewListPage:
        """`feedback_for_me=true`: the caller's nominated draft reviews."""
        if status is not None and status is not ReviewStatus.DRAFT:
            # The spec pins "cannot be combined" as a 422 validation error.
            raise PerformanceValidationError()
        if employee_id is not None:
            if await self._repo.employee_facts(identity.tenant_id, employee_id) is None:
                raise EmployeeNotFoundError()
        if identity.employee_id is None:
            return ReviewListPage(items=[], page=page.make_page(0))

        filters = ReviewFilters(
            employee_id=employee_id,
            status=ReviewStatus.DRAFT,
            feedback_provider_id=identity.employee_id,
        )
        items, total = await self._repo.list_reviews(identity.tenant_id, filters, page)
        return ReviewListPage(
            items=self._serialize_rows(
                identity,
                items,
                provider_worklist=True,
                visible_ids=await self._manager_visible(identity),
            ),
            page=page.make_page(total),
        )

    async def _list_scope(
        self, identity: Identity, employee_id: UUID | None
    ) -> tuple[
        Sequence[UUID] | None,
        Sequence[UUID] | None,
        Sequence[UUID] | None,
        UUID | None,
        ReviewStatus | None,
    ]:
        """Effective list filters (visible, completed-only, hidden, author, status).

        Employee/Finance are pure subjects: own rows only, silently narrowed to
        `completed` (pinned query semantics). A Manager sees direct reports in
        every state, their own subject rows only when completed, and their
        authored reviews anywhere. Management is unscoped except for the
        universal rule that their own non-completed rows never surface.
        """
        hidden = [identity.employee_id] if identity.employee_id is not None else None
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            if identity.employee_id is None:
                if employee_id is not None:
                    raise EmployeeNotFoundError()
                return [], None, None, None, ReviewStatus.COMPLETED
            if employee_id is not None and employee_id != identity.employee_id:
                raise EmployeeNotFoundError()
            return (
                [identity.employee_id],
                None,
                hidden,
                None,
                ReviewStatus.COMPLETED,
            )

        if identity.role is Role.MANAGER:
            visible = set(await self._manager_visible(identity))
            if employee_id is not None and employee_id not in visible:
                raise EmployeeNotFoundError()
            direct_reports = [
                visible_id
                for visible_id in visible
                if visible_id != identity.employee_id
            ]
            completed_only = (
                [identity.employee_id]
                if identity.employee_id is not None and identity.employee_id in visible
                else []
            )
            return direct_reports, completed_only, hidden, identity.actor_id, None

        if employee_id is not None:
            # Tenant-wide viewer: the filter target must still exist here.
            if await self._repo.employee_facts(identity.tenant_id, employee_id) is None:
                raise EmployeeNotFoundError()
        return None, None, hidden, None, None

    def _serialize_rows(
        self,
        identity: Identity,
        rows: Sequence[Review],
        *,
        provider_worklist: bool,
        visible_ids: set[UUID] | None = None,
    ) -> list[Review]:
        """Per-row visibility for lists (no further queries needed).

        Normal lists only contain rows the caller may see in full (team/author/
        management) or their own completed subject rows. The provider worklist
        additionally contains draft reviews the caller was nominated on: those
        serialize as the shell unless the caller has fuller standing.
        """
        items: list[Review] = []
        for row in rows:
            is_subject = (
                identity.employee_id is not None
                and identity.employee_id == row.employee_id
            )
            if provider_worklist and not is_management(identity):
                if (
                    is_subject
                    or visible_ids is None
                    or row.employee_id not in visible_ids
                ):
                    items.append(_review_shell(row))
                    continue
                items.append(row)
                continue
            items.append(_without_providers(row) if is_subject else row)
        return items

    # --- internals -----------------------------------------------------------

    async def _ensure_reviewable_subject(
        self, identity: Identity, employee_id: UUID
    ) -> None:
        """The subject exists in the tenant, is in scope, and is active."""
        facts = await self._repo.employee_facts(identity.tenant_id, employee_id)
        if facts is None:
            raise EmployeeNotFoundError()
        if identity.role is Role.MANAGER:
            visible = await self._manager_visible(identity)
            if employee_id not in visible:
                raise EmployeeNotFoundError()
        if not facts.active:
            raise EmployeeNotActiveError()

    async def _validate_providers(
        self,
        identity: Identity,
        *,
        subject_id: UUID,
        author_employee_id: UUID | None,
        provider_ids: Sequence[UUID],
    ) -> list[UUID]:
        """Dedupe and validate the nomination list.

        Same-tenant, active employees only; the subject, the acting user and the
        review author's employee link are never eligible (`409
        feedback_provider_invalid`) — an HR editor must not slip the original
        author into the peer set. A foreign/unknown id is `404` so a cross-tenant
        probe learns nothing.
        """
        providers = list(dict.fromkeys(provider_ids))
        ineligible = {subject_id}
        for candidate in (identity.employee_id, author_employee_id):
            if candidate is not None:
                ineligible.add(candidate)
        facts_by_id = await self._repo.employee_facts_many(
            identity.tenant_id, providers
        )
        for provider_id in providers:
            if provider_id in ineligible:
                raise FeedbackProviderInvalidError()
            facts = facts_by_id.get(provider_id)
            if facts is None:
                raise EmployeeNotFoundError()
            if not facts.active:
                raise FeedbackProviderInvalidError()
        return providers

    async def _manageable_review(
        self, identity: Identity, review_id: UUID
    ) -> tuple[Review, UUID]:
        """Fetch a review the caller may mutate plus its author user id.

        Mutation is the author's or HR/Admin's; the subject is always refused
        (never self) even when their role could otherwise manage reviews.
        """
        record = await self._repo.get_review_with_author(identity.tenant_id, review_id)
        if record is None:
            raise ReviewNotFoundError()
        review = record.review
        if (
            identity.employee_id is not None
            and identity.employee_id == review.employee_id
        ):
            raise PerformanceDeniedError()
        ensure_can_nominate(identity, is_author=record.author_id == identity.actor_id)
        return review, record.author_id

    async def _serialize_review(
        self, identity: Identity, review: Review, author_id: UUID | None
    ) -> Review:
        """The pinned visibility matrix, one row at a time."""
        if (
            identity.employee_id is not None
            and identity.employee_id == review.employee_id
        ):
            # The subject sees only completed reviews; a draft id is a 404.
            if review.status is not ReviewStatus.COMPLETED:
                raise ReviewNotFoundError()
            return _without_providers(review)
        if is_management(identity) or (
            author_id is not None and author_id == identity.actor_id
        ):
            return review
        if (
            identity.employee_id is not None
            and review.employee_id in await self._manager_visible(identity)
        ):
            return review
        if identity.employee_id is not None and identity.employee_id in (
            review.feedback_provider_ids or []
        ):
            # A nominated provider may see the shell of the draft they must
            # comment on — never its rating or text.
            if review.status is ReviewStatus.DRAFT:
                return _review_shell(review)
        raise ReviewNotFoundError()

    async def _manager_visible(self, identity: Identity) -> set[UUID]:
        """Self + direct reports for a Manager; empty for everyone else."""
        if identity.role is not Role.MANAGER or identity.employee_id is None:
            return set()
        return set(
            await self._scope.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
        )

    async def _write_audit(
        self,
        identity: Identity,
        action: str,
        entity_type: str,
        entity_id: UUID,
        now: datetime,
        changes: dict[str, Any],
    ) -> None:
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                occurred_at=now,
                changes=changes,
            )
        )


class SkillService:
    """Skill catalog and per-employee skill rows (T7).

    The catalog is tenant reference data under ADR 0007 soft-delete semantics:
    archive/restore are no-ops when the row is already in the target state (no
    audit entry), and uniqueness spans archived rows so a restore never
    conflicts. Archiving a skill that employees already hold is allowed (the
    documented divergence): history stays readable and editable, only new
    assignment is blocked.

    Employee-skill rows are the employee's own, their manager's (self + direct
    reports) and HR/Admin/Super Admin's to manage. Out-of-scope, foreign and
    unknown ids are `404` (`employee_not_found` / `skill_not_found`), never a
    denied leak. Record/update/remove write one audit entry each; they emit no
    events (no workflow state). Notes and skill descriptions are PII: audit
    records the `"changed"` marker, never the value.

    The service takes no `EventOutbox`: the pinned contract has no skill events.
    """

    def __init__(
        self,
        repo: PerformanceRepository,
        scope: EmployeeScope,
        audit: AuditWriter,
    ) -> None:
        self._repo = repo
        self._scope = scope
        self._audit = audit

    # --- catalog -------------------------------------------------------------

    async def create_skill(
        self, identity: Identity, payload: SkillCreate, now: datetime
    ) -> Skill:
        """Add a catalog skill; a duplicate name (archived included) is `409`."""
        ensure_can_manage_skills(identity)
        if await self._repo.skill_name_taken(identity.tenant_id, payload.name):
            raise SkillNameTakenError()
        skill = await self._repo.create_skill(
            identity.tenant_id,
            uuid7(),
            name=payload.name,
            description=payload.description,
        )
        changes: dict[str, Any] = {"name": {"old": None, "new": skill.name}}
        if skill.description is not None:
            changes["description"] = PII_CHANGED  # PII: never the value
        await self._write_audit(identity, "skill.created", skill.id, now, changes)
        return skill

    async def list_skills(
        self,
        identity: Identity,
        *,
        q: str | None = None,
        include_archived: bool = False,
        page: PageParams,
    ) -> SkillListPage:
        """Paged catalog, name-ordered; archived rows only on request."""
        filters = SkillFilters(q=q, include_archived=include_archived)
        items, total = await self._repo.list_skills(identity.tenant_id, filters, page)
        return SkillListPage(items=list(items), page=page.make_page(total))

    async def update_skill(
        self,
        identity: Identity,
        skill_id: UUID,
        payload: SkillUpdate,
        now: datetime,
    ) -> Skill:
        """Rename/re-describe a catalog skill; a duplicate name is `409`."""
        ensure_can_manage_skills(identity)
        existing = await self._skill(skill_id, identity)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            # A no-op edit changes no fact: no write, no audit.
            return existing
        if "name" in changes and await self._repo.skill_name_taken(
            identity.tenant_id, changes["name"], exclude_id=skill_id
        ):
            raise SkillNameTakenError()

        updated = await self._repo.update_skill(
            identity.tenant_id, skill_id, now=now, **changes
        )
        if updated is None:
            raise SkillNotFoundError()

        audit_changes: dict[str, Any] = {}
        for field in changes:
            if field == "description":
                audit_changes[field] = PII_CHANGED  # PII: never the value
            else:
                audit_changes[field] = {
                    "old": getattr(existing, field),
                    "new": getattr(updated, field),
                }
        await self._write_audit(identity, "skill.updated", skill_id, now, audit_changes)
        return updated

    async def archive_skill(
        self, identity: Identity, skill_id: UUID, now: datetime
    ) -> Skill:
        """Soft delete; already-archived is a `200` no-op without an audit row."""
        ensure_can_manage_skills(identity)
        # Read first so the audit records the state the row actually had; the
        # conditional update remains the race backstop.
        existing = await self._skill(skill_id, identity)
        result = await self._repo.archive_skill(identity.tenant_id, skill_id, now=now)
        if result is None:
            raise SkillNotFoundError()
        if result.changed:
            await self._write_audit(
                identity,
                "skill.archived",
                skill_id,
                now,
                {
                    "archived_at": {
                        "old": _value(existing.archived_at),
                        "new": _value(result.skill.archived_at),
                    }
                },
            )
        return result.skill

    async def restore_skill(
        self, identity: Identity, skill_id: UUID, now: datetime
    ) -> Skill:
        """Clear `archived_at`; already-active is a `200` no-op without audit."""
        ensure_can_manage_skills(identity)
        existing = await self._skill(skill_id, identity)
        result = await self._repo.restore_skill(identity.tenant_id, skill_id, now=now)
        if result is None:
            raise SkillNotFoundError()
        if result.changed:
            await self._write_audit(
                identity,
                "skill.restored",
                skill_id,
                now,
                {
                    "archived_at": {
                        "old": _value(existing.archived_at),
                        "new": _value(result.skill.archived_at),
                    }
                },
            )
        return result.skill

    # --- employee skills -----------------------------------------------------

    async def record_employee_skill(
        self,
        identity: Identity,
        employee_id: UUID,
        payload: EmployeeSkillCreate,
        now: datetime,
    ) -> EmployeeSkill:
        """Attach an active catalog skill; archived skills cannot be attached."""
        ensure_can_record_skills(identity)
        await self._ensure_employee(identity, employee_id)
        skill = await self._skill(payload.skill_id, identity)
        if skill.archived_at is not None:
            raise SkillArchivedError()
        if (
            await self._repo.get_employee_skill(
                identity.tenant_id, employee_id, payload.skill_id
            )
            is not None
        ):
            # Pre-check the pinned duplicate; the composite primary key stays
            # the race backstop (mapped in the adapter).
            raise EmployeeSkillExistsError()

        row = await self._repo.record_employee_skill(
            identity.tenant_id,
            employee_id=employee_id,
            skill_id=payload.skill_id,
            proficiency=payload.proficiency,
            note=payload.note,
        )
        changes: dict[str, Any] = {
            "employee_id": {"old": None, "new": str(employee_id)},
            "skill_id": {"old": None, "new": str(payload.skill_id)},
            "proficiency": {"old": None, "new": row.proficiency},
        }
        if row.note is not None:
            changes["note"] = PII_CHANGED  # PII: never the value
        await self._write_audit(
            identity,
            "employee_skill.recorded",
            payload.skill_id,
            now,
            changes,
            entity_type="employee_skill",
        )
        return row

    async def list_employee_skills(
        self, identity: Identity, employee_id: UUID
    ) -> EmployeeSkillList:
        """The employee's skill rows, scoped own/team/tenant like goals."""
        await self._ensure_employee(identity, employee_id)
        items = await self._repo.list_employee_skills(identity.tenant_id, employee_id)
        return EmployeeSkillList(items=list(items))

    async def update_employee_skill(
        self,
        identity: Identity,
        employee_id: UUID,
        skill_id: UUID,
        payload: EmployeeSkillUpdate,
        now: datetime,
    ) -> EmployeeSkill:
        """Replace proficiency/note in place; archived skills stay editable."""
        ensure_can_record_skills(identity)
        await self._ensure_employee(identity, employee_id)
        existing = await self._repo.get_employee_skill(
            identity.tenant_id, employee_id, skill_id
        )
        if existing is None:
            raise SkillNotFoundError()

        updated = await self._repo.update_employee_skill(
            identity.tenant_id,
            employee_id,
            skill_id,
            proficiency=payload.proficiency,
            note=payload.note,
            now=now,
        )
        if updated is None:
            # The row was removed between the read and the write.
            raise SkillNotFoundError()

        changes: dict[str, Any] = {
            "proficiency": {
                "old": existing.proficiency,
                "new": updated.proficiency,
            }
        }
        if "note" in payload.model_fields_set:
            changes["note"] = PII_CHANGED  # PII: never the value
        await self._write_audit(
            identity,
            "employee_skill.updated",
            skill_id,
            now,
            changes,
            entity_type="employee_skill",
        )
        return updated

    async def remove_employee_skill(
        self, identity: Identity, employee_id: UUID, skill_id: UUID, now: datetime
    ) -> None:
        """Delete the row; audited, never evented (no workflow state)."""
        ensure_can_record_skills(identity)
        await self._ensure_employee(identity, employee_id)
        existing = await self._repo.get_employee_skill(
            identity.tenant_id, employee_id, skill_id
        )
        if existing is None:
            raise SkillNotFoundError()

        removed = await self._repo.remove_employee_skill(
            identity.tenant_id, employee_id, skill_id
        )
        if not removed:
            raise SkillNotFoundError()

        await self._write_audit(
            identity,
            "employee_skill.removed",
            skill_id,
            now,
            {
                "employee_id": {"old": str(employee_id), "new": None},
                "skill_id": {"old": str(skill_id), "new": None},
                "proficiency": {"old": existing.proficiency, "new": None},
            },
            entity_type="employee_skill",
        )

    # --- internals -----------------------------------------------------------

    async def _skill(self, skill_id: UUID, identity: Identity) -> Skill:
        """Tenant-scoped skill detail; foreign and unknown are the same `404`."""
        skill = await self._repo.get_skill(identity.tenant_id, skill_id)
        if skill is None:
            raise SkillNotFoundError()
        return skill

    async def _ensure_employee(self, identity: Identity, employee_id: UUID) -> None:
        """The employee exists in the tenant and is visible to the caller."""
        facts = await self._repo.employee_facts(identity.tenant_id, employee_id)
        if facts is None or not await self._employee_visible(identity, employee_id):
            # Existence is not leaked to a caller without standing (404).
            raise EmployeeNotFoundError()

    async def _employee_visible(self, identity: Identity, employee_id: UUID) -> bool:
        """Own for Employee/Finance, self + direct reports for Manager, else all."""
        if identity.role in (Role.EMPLOYEE, Role.FINANCE):
            return (
                identity.employee_id is not None and employee_id == identity.employee_id
            )
        if identity.role is Role.MANAGER:
            if identity.employee_id is None:
                return False
            visible = await self._scope.visible_employee_ids(
                identity.tenant_id, identity.employee_id
            )
            return employee_id in visible
        return True  # hr / admin / super_admin

    async def _write_audit(
        self,
        identity: Identity,
        action: str,
        skill_id: UUID,
        now: datetime,
        changes: dict[str, Any],
        *,
        entity_type: str = "skill",
    ) -> None:
        await self._audit.write(
            AuditEntryCreate(
                tenant_id=identity.tenant_id,
                actor_id=identity.actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=skill_id,
                occurred_at=now,
                changes=changes,
            )
        )
