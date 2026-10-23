# 20. Recruitment hiring seam (Recruitment → Employees)

- **Status:** Accepted
- **Date:** 2026-10-23

## Context

Recruitment (`.ai/features/recruitment.md`, PO Decision 2) closes the jobs →
employees loop: a hire must create the Employee record instead of ending in
manual re-entry. The Recruitment context must not import Employees internals
(AGENTS.md architecture rule 3, `.ai/domain.md` context map), and the hire has
to be atomic — a candidate that flips to `HIRED` while its Employee creation
fails would be an unaudited half-fact, and an Employee created while the
candidate stays `OFFERED` would be a phantom hire.

This is a **write-direction** seam (ADR 0018 covered write flips between
payroll and overtime; ADR 0014 covered read joins), and it is the first seam
whose callee is another domain's **service** rather than a repository join.

Alternatives considered:

- **Recruitment writes the `employees`/`employments` tables directly.** Cross-
  domain table writes; skips `EmployeeService.create`'s email/code uniqueness,
  org-reference validation, manager validation, code generation, employment
  period, `employee.created` event and audit entry. Rejected.
- **Recruitment calls an Employees HTTP endpoint.** A second request cannot
  join the candidate's transaction, so the two facts could diverge. Rejected.
- **Event-driven eventual hire** (`candidate.hired` consumed by Employees).
  The API response needs the `employee_id`, and the candidate must stay
  `OFFERED` when the seam fails; eventual consistency cannot express that
  atomically. Rejected.
- **A shared orchestration service above both domains.** New cross-domain
  component for one use case, with no clear owner of the state machine.
  Rejected.

## Decision

1. **Recruitment owns the port; the adapter lives in infrastructure.**
   `app/domains/recruitment/repository.py` defines `EmployeeHirePort` plus the
   `EmployeeHirePayload` dataclass. The domain imports neither
   `app.domains.employees` nor `app.infrastructure`. The Postgres adapter
   (`app/infrastructure/postgres/recruitment_hire.py`,
   `PostgresEmployeeHirePort`) is wired in `app/dependencies.py` (ADR 0014 /
   ADR 0018 wiring pattern).
2. **`create_employee(tenant_id, actor_id, payload, now) -> UUID` calls
   `EmployeeService.create` on the caller's `AsyncSession`.** The adapter builds
   the effective `CreateEmployee` (candidate names/email/phone plus hire
   overrides, the job's `department_id`/`position_id`/`location_id`, manager,
   employment type, hire date, employee code) and invokes the Employees service
   with an `Identity` carrying the real tenant, the real acting user id and
   `role=Role.HR` (the service does not read `role`). Because the service only
   `flush()`es, the employee row, its first employment period, its
   `employee.created` envelope/audit, the candidate's `HIRED` flip and its
   `candidate.hired` envelope/audit all commit in the one request transaction
   (ADR 0012).
3. **Seam errors propagate unchanged.** `employee_email_taken`,
   `employee_code_taken`, `employee_manager_invalid` (400),
   `department_not_found`, `position_not_found`, `location_not_found` and
   `transfer_target_archived` surface with the Employees codes; the adapter
   does not remap them. Any failure rolls the whole transaction back and the
   candidate remains `OFFERED`.
4. **State and capacity guards stay on the Recruitment side.** `hire` locks the
   candidate, requires `OFFERED`, loads and locks the job, requires `OPEN`, and
   counts already-hired candidates against `openings` before calling the port.
   The port never inspects recruitment state.
5. **No duplicate facts.** Employees emits `employee.created` as it does for any
   employee; Recruitment emits `candidate.hired` with the returned
   `employee_id`. Neither domain emits the other's event.
6. **Test seam.** Unit tests use a fake `EmployeeHirePort` that can raise every
   seam error; integration tests exercise the real adapter and assert the
   employee + employment rows, both outbox envelopes, the email-taken rollback
   and the openings guard.

## Consequences

- Hiring is one audited, atomic flow: a failed seam leaves no employee and no
  state change behind, and a concurrent opening claim is serialized by the job
  lock.
- Recruitment depends only on a small protocol, so Employees can evolve its
  service internals without Recruitment importing them; the adapter is the one
  place that knows both sides.
- The Employees service is now a seam callee as well as an HTTP surface; a
  change to `EmployeeService.create` semantics affects recruitment hires and
  must keep the seam tests green.
- The adapter must share the request session (never open its own), otherwise
  the atomicity guarantee silently disappears — the integration rollback test
  is the guard.
