# 21. Skill archive is never blocked by use

- **Status:** Accepted
- **Date:** 2026-10-27

## Context

The Performance skill catalog is tenant reference data under ADR 0007's
soft-delete semantics: no DELETE route, `archived_at` marks an archived row,
archive/restore transitions, and name uniqueness `(tenant_id, name)` spans
archived rows so a restore never conflicts (`.ai/features/performance.md`,
Business rules — Skills).

ADR 0007 also pins the organization-reference rule: a department (and its
siblings) cannot be archived while active employees still reference it
(`409 department_archived_in_use`; organizations Risk 1). Applying that rule to
skills would mean an employee-skill row is "use", so any skill ever recorded on
anyone could never be archived. Historical skill rows are deliberately kept —
an employee's record is a fact about their past — so the catalog would grow
forever and cleanup would be held hostage by history. That is the opposite of
what a catalog is for.

Alternatives considered:

- **Block archive while `performance_employee_skills` references the skill**
  (parity with departments). Rejected: it makes the catalog unmaintainable once
  the tenant has real history, and the history itself is not affected by
  archiving the catalog row.
- **Cascade-delete the employee-skill rows on archive.** Rejected: it erases
  employee facts and audit history; the feature spec explicitly promises the
  label stays forever.
- **Keep the skill active forever and hide it from the picker some other way.**
  Rejected: a second lifecycle concept for the same purpose.
- **A separate `retired` flag instead of `archived_at`.** Rejected: duplicates
  ADR 0007's soft-delete vocabulary for one catalog.

## Decision

1. **Archiving a skill is always allowed**, regardless of how many
   `performance_employee_skills` rows reference it.
2. **Existing employee-skill rows keep their label and stay readable and
   editable forever.** They are never cascaded, rewritten or hidden.
3. **Only new assignment is blocked:** attaching an archived skill to an
   employee returns `409 skill_archived`. Updating the proficiency/note of an
   existing row on an archived skill remains allowed.
4. **Archive/restore are no-ops on an already-archived / already-active skill**
   (`200`, no audit entry), following ADR 0007.
5. **Name uniqueness spans archived rows.** An archived skill keeps its name
   reserved, so restore never conflicts; a duplicate name — archived included —
   is `409 skill_name_taken`.
6. **No DELETE route.** The only catalog mutations are create/rename/archive/
   restore (ADR 0007 soft delete).

This is the one deliberate divergence from ADR 0007's in-use archive rule.
Everything else — soft delete, `archived_at` reads via `include_archived=true`,
default listings and pickers hiding archived rows, no-op repeats, audit
semantics — follows it.

## Consequences

- A tenant can retire a skill from the catalog without losing or rewriting the
  history on any employee profile; the picker stops offering it, the record
  stays.
- A future reader must not "fix" this back into department parity: the archive
  path intentionally has no in-use guard. The behavior is pinned by
  `apps/api/tests/unit/performance/test_skills_service.py`
  (`test_update_and_remove_employee_skill_are_audited`) for the archive-in-use
  success, and by
  `apps/api/tests/integration/test_performance_skills.py`
  (`test_catalog_and_employee_skill_hop`) for the blocked re-assignment.
- If a future reference-data catalog has no historical attachment table the
  ADR 0007 rule is still the default; this decision is scoped to skills because
  their use is historical by design.
- The archived name remains reserved, which is the cost of conflict-free
  restore and is visible in the pinned `409 skill_name_taken` behavior.
