"""Use cases for the Performance domain — goals (T5).

Rules implemented here (pinned in `.ai/features/performance.md`):

- **Goal lifecycle**: `(create) → DRAFT → ACTIVE → COMPLETED`, with
  `CANCELLED` reachable from `DRAFT`/`ACTIVE`. `COMPLETED`/`CANCELLED` are
  terminal: `activate` only from `draft` (`409 goal_not_draft`), `complete` only
  from `active` (`409 goal_not_active`), `update`/`cancel` on a terminal row are
  `409 goal_terminal`. Every transition is one conditional repository update
  plus one event envelope and one audit entry in the same transaction (ADR
  0012); `None` from the conditional update is a lost race and maps to the
  same pinned wrong-state error, never a silent overwrite.
- **Authorship and scope**: the subject may manage their own goals; a Manager
  manages self + direct reports through the shared `EmployeeScope` port;
  HR/Admin/Super Admin manage anyone. Out-of-scope and foreign subjects are
  `404 employee_not_found` (never `403`); Finance has no management standing
  (`403 performance_denied`). `GoalCreate.employee_id` omitted defaults to the
  actor's employee link; a missing link fails closed with `403`
  `performance_denied` (overtime's missing-link precedent).
- **Progress is a field update**, not a transition: it emits no event but is
  audited (`goal.updated`). Title, description and progress are editable only
  while `draft|active`; terminal goals never change again. Completed never
  auto-forwards `progress_pct`.
- **PII** (`.ai/security.md`): goal `title`/`description` are never logged,
  emitted or written into audit `changes` values — the audit records the
  `"changed"` marker only. Events carry ids and state only.
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
    GoalNotActiveError,
    GoalNotDraftError,
    GoalNotFoundError,
    GoalTerminalError,
    PerformanceDeniedError,
)
from app.domains.performance.policies import ensure_can_manage_goals
from app.domains.performance.repository import (
    EmployeeScope,
    GoalFilters,
    PerformanceRepository,
)
from app.domains.performance.schemas import (
    Goal,
    GoalCreate,
    GoalListPage,
    GoalStatus,
    GoalUpdate,
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
