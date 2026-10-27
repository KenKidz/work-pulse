# Feature: Performance

- **Status:** done
- **Phase:** 4+ (the Performance context of `.ai/domain.md`; skills were deferred
  here by product decision 4, `.ai/product.md`)
- **Size:** M
- **Surfaces:** api+web+events
- **Links:** `.ai/domain.md` (Performance context: Goal, Review, Skill; event
  catalog; invariants 1–6) · `.ai/product.md` (Manager "run reviews",
  decision 4: skills → Performance) · `.ai/security.md` (roles matrix, PII) ·
  ADR 0007 (reference-data soft delete — the skill catalog) · ADR 0010
  (identity, `employee_id`) · ADR 0012 (outbox) · ADR 0021 (skill archive is
  never blocked by use — the documented ADR 0007 divergence) · `leave.md` /
  `overtime.md` (manager scope = self + direct reports; workflow precedent) ·
  `recruitment.md` (events-only precedent) · `i18n.md` (catalog conventions)

## Problem

Managers run performance evaluations nowhere: goals live in chat threads and
spreadsheets, a review is written in a doc with no trail, peer input is
collected by memory, and an employee's skills are unknown to anyone but their
manager. WorkPulse already treats every workforce fact as an auditable event —
performance is the last major context in `.ai/domain.md` with no home. This
feature gives it one: employees own goals and skills, managers run reviews with
peer feedback, and the finalized review becomes an immutable, auditable fact
(`performance.review_completed`).

## Business rules

- **Goals — lifecycle** (every transition is an event + an audit entry):

  ```text
  (create) ─► DRAFT ─► ACTIVE ─► COMPLETED
                 │         │
                 └─────────┴──────► CANCELLED
  ```

  `COMPLETED` and `CANCELLED` are terminal; a terminal goal is never re-opened.
  `activate` only from `DRAFT` (`409 goal_not_draft`), `complete` only from
  `ACTIVE` (`409 goal_not_active`), `cancel` from `DRAFT` or `ACTIVE`.
- **Goals — authorship and fields**: a goal belongs to one employee
  (`employee_id`, the subject). The subject may create and manage **their own**
  goals; a manager may manage goals of self + direct reports (the same scope
  port as `leave.md`/`overtime.md`); HR / Admin / Super Admin manage anyone's.
  Fields: `title` (1..200), `description` (optional, 1..2000), `due_date`
  (optional `YYYY-MM-DD`), `progress_pct` (int 0..100, default 0). Progress is
  a field update, not a state transition: it emits no event but is audited.
  Title, description and progress may be edited only while `draft|active`.
  Past due dates are allowed (goals are plans, not claims — unlike overtime).
- **Reviews — lifecycle**: `DRAFT ─► COMPLETED | CANCELLED` (`COMPLETED`,
  `CANCELLED` terminal). One review evaluates one employee (`employee_id`, the
  subject) and is authored by a manager or HR. Fields: `period` (optional label,
  1..50, e.g. `2026-H1`), `rating` (integer 1..5), `strengths` (optional,
  1..2000), `improvements` (optional, 1..2000), `feedback_provider_ids`
  (≤ 10 employees, editable only while `draft`).
- **Reviews — who runs them** (security matrix: *Manager — run performance
  evaluations*): the author is the subject's team manager, or HR / Admin /
  Super Admin. **Never self**: `employee_id` equal to the actor's own → `403`
  (the "never self-decision" rule of `overtime.md`). The subject must be an
  active employee (`409 employee_not_active`).
- **Reviews — completion**: `complete` is allowed only from `DRAFT` and only
  when `rating` is present (`409 review_incomplete`), stamps `completed_at`
  and emits `performance.review_completed`. A completed review is **immutable**
  — a correction is a new review. `cancel` is allowed only from `DRAFT`.
- **Reviews — visibility**: the subject employee sees only their **completed**
  reviews (draft reviews are hidden from them entirely). The author sees their
  own reviews in every state; managers see self + direct reports; HR / Admin /
  Super Admin see all. Employees' role sees own only.
- **Feedback is tied to reviews**: while a review is `draft`, its author
  nominates `feedback_provider_ids` — active employees who are neither the
  subject nor the author (`409 feedback_provider_invalid`). A nominated
  provider files **one** feedback comment (1..2000) on the review while it is
  `draft` (`409 review_not_draft` after complete/cancel; `409
  feedback_already_submitted` on a second attempt). Feedback is immutable
  after submission and emits `performance.feedback_submitted`.
- **Feedback — visibility**: the review author and HR / Admin / Super Admin see
  all feedback on the review; a provider sees only their own. **The subject
  employee never sees feedback** (it is peer input to the manager's judgment,
  like a leave reason is to an approver): `GET …/feedback` as the subject →
  `403 feedback_access_denied`.
- **Skills — catalog** is per-tenant reference data under ADR 0007 semantics:
  soft delete via `archived_at`, archive/restore transitions (no DELETE route),
  name uniqueness `(tenant_id, name)` spans **all** rows including archived, and
  archived skills stay readable (`include_archived=true`) but leave default
  listings and pickers. The one deliberate divergence from ADR 0007's
  department rule: archiving a skill **in use is allowed** — employee skill rows
  keep their label forever, and only *new* assignment is blocked (`409
  skill_archived`); catalog cleanup must not be held hostage by history. Skill
  fields: `name` (1..100), `description` (optional, 1..500).
- **Employee skills** attach one catalog skill to one employee with
  `proficiency` (integer 1..5) and an optional `note` (1..500). At most one row
  per `(employee, skill)` — a second attach is `409 employee_skill_exists`;
  proficiency/note are updated in place. The employee themself, their manager
  (self + direct reports) and HR / Admin / Super Admin may add, update and
  remove rows. A removed row is audited, never evented (no workflow state).
- **PII**: `title`, `description` (goal), `period`, `strengths`, `improvements`
  and `note` (skill) are sensitive — never in logs, error messages, event
  payloads or audit `changes` values (ids + state only). Ratings are evaluative
  values: they never appear in events either.
- Tenant isolation (invariant 1), policies in
  `domains/performance/policies.py` (AGENTS.md rule 12) and time-as-a-parameter
  hold as everywhere else.

## Scope

- Goals: create / update / activate / complete / cancel, role-scoped lists.
- Reviews: create / update (draft) / complete / cancel, role-scoped lists;
  the subject's read-only "my reviews".
- Feedback: nomination of providers on a draft review, one submission per
  provider, role-scoped visibility.
- Skills: tenant catalog (create / rename / archive / restore) and per-employee
  skill rows (attach / update / remove).
- Eight event contracts under `contracts/events/performance/` written **before**
  any producer, produced through the `EventOutbox` port in the same transaction
  as the transition (ADR 0012). Events-only scope: notification triggers and
  performance analytics are follow-up consumers (`recruitment.md` decision 5
  precedent).
- Web `/performance` workspace (Goals · Reviews · Skills) with an i18n
  `performance` catalog surface (en + vi) and a nav entry for every role.

## Out of scope

- Review cycles, windows, deadlines and assignments (HR-defined cycles were the
  rejected alternative — see `## Decisions`).
- Self-assessment steps and employee acknowledgement/sign-off of a review.
- Standalone (kudos-style) feedback outside a review.
- OKR trees, parent/child goal alignment, company-level goals.
- Skill endorsements; skill-to-job matching; skills in recruitment.
- Reopening or editing a completed review; rating calibration, curves or
  cross-team calibration sessions.
- Goal/review reminders and due-date notifications — ADR 0015's trigger map is
  untouched; a follow-up extension adds performance triggers.
- Analytics & Reports projections of performance (no ClickHouse work here).
- Any effect on payroll, promotion or employment records.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/performance/` |
| API router | `apps/api/app/api/v1/performance.py` |
| Events | `contracts/events/performance/` |
| Workers | none (events-only scope) |
| Web feature | `apps/web/features/performance/` |
| Web routes | `apps/web/app/(dashboard)/performance/` |
| Web i18n | `apps/web/messages/en/performance.json` · `apps/web/messages/vi/performance.json` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| Web tests | `apps/web/tests` |
| E2E | `tests/e2e/performance.spec.ts` |

## Roles & access

These capability rows join `.ai/security.md`'s matrix in the implementation
change. `own` = only their own data, `team` = self + direct reports (the scope
port shared with leave/overtime/reports).

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View own goals / completed reviews / skills | own | own | own | own | own | own |
| Manage goals | own | team | Y | — | Y | Y |
| Run reviews (create / complete / cancel) | — | team | Y | — | Y | Y |
| View reviews of others | — | team | Y | — | Y | Y |
| Nominate providers / view feedback | — | own reviews | Y | — | Y | Y |
| File feedback | nominated only | nominated only | nominated only | nominated only | nominated only | nominated only |
| Manage skill catalog | — | — | Y | — | Y | Y |
| Record employee skills | own | team | Y | — | Y | Y |

## API contract (pinned before coding)

All operationIds are the orval hook names and are pinned: the router sets them
as `operation_id` and the generated `@workpulse/api-client` hooks must match
exactly. JSON status values are lowercase. Pagination is the house `PageParams`
clamp shape (clamp, never an error). Every response row carries ids + state +
timestamps; free text appears only in the field documented for it.

### Goals

| Method & path | operationId | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/performance/goals` | `createGoal` | `GoalCreate` | `201 Goal` (`draft`) |
| `GET /api/v1/performance/goals` | `listGoals` | `?employee_id=&status=&page=&page_size=` | `200 {"items": Goal[], "page": Page}` |
| `GET /api/v1/performance/goals/{id}` | `getGoal` | — | `200 Goal` |
| `PATCH /api/v1/performance/goals/{id}` | `updateGoal` | `GoalUpdate` | `200 Goal` |
| `POST /api/v1/performance/goals/{id}/activate` | `activateGoal` | — | `200 Goal` (`active`) |
| `POST /api/v1/performance/goals/{id}/complete` | `completeGoal` | — | `200 Goal` (`completed`) |
| `POST /api/v1/performance/goals/{id}/cancel` | `cancelGoal` | — | `200 Goal` (`cancelled`) |

`GoalCreate` / `GoalUpdate`: `title` (1..200, required on create),
`description` (1..2000), `due_date` (`YYYY-MM-DD`), `progress_pct` (0..100),
and on create `employee_id` (defaults to the actor's own `identity.employee_id`).
`Goal`: `id`, `employee_id`, `title`, `description`, `due_date`, `progress_pct`,
`status` (`draft|active|completed|cancelled`), `created_at`, `updated_at`,
`completed_at`.

### Reviews & feedback

| Method & path | operationId | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/performance/reviews` | `createReview` | `ReviewCreate` | `201 Review` (`draft`) |
| `GET /api/v1/performance/reviews` | `listReviews` | `?employee_id=&status=&feedback_for_me=&page=&page_size=` | `200 {"items": Review[], "page": Page}` |
| `GET /api/v1/performance/reviews/{id}` | `getReview` | — | `200 Review` |
| `PATCH /api/v1/performance/reviews/{id}` | `updateReview` | `ReviewUpdate` | `200 Review` |
| `POST /api/v1/performance/reviews/{id}/complete` | `completeReview` | — | `200 Review` (`completed`) |
| `POST /api/v1/performance/reviews/{id}/cancel` | `cancelReview` | — | `200 Review` (`cancelled`) |
| `POST /api/v1/performance/reviews/{id}/feedback` | `submitReviewFeedback` | `ReviewFeedbackCreate` | `201 ReviewFeedback` |
| `GET /api/v1/performance/reviews/{id}/feedback` | `listReviewFeedback` | — | `200 {"items": ReviewFeedback[]}` |

`ReviewCreate`: `employee_id` (required), `period` (1..50),
`feedback_provider_ids` (≤ 10 uuids). `ReviewUpdate` (draft only): `period`,
`rating` (1..5), `strengths` (1..2000), `improvements` (1..2000),
`feedback_provider_ids`. `Review`: `id`, `employee_id`, `period`, `rating`,
`strengths`, `improvements`, `status` (`draft|completed|cancelled`),
`feedback_provider_ids`, `created_at`, `updated_at`, `completed_at` — the
subject-facing serialization omits `feedback_provider_ids`. `ReviewFeedbackCreate`:
`comment` (1..2000). `ReviewFeedback`: `id`, `review_id`, `author_id`,
`comment`, `created_at`.

Query semantics (pinned):

- `employee_id` filters by subject; a foreign (other-tenant / unknown)
  `employee_id` → `404 employee_not_found`.
- `feedback_for_me=true` returns reviews where the caller is a nominated
  provider (the peer's worklist); it cannot be combined with `status` other than
  `draft`.
- Lists are visibility-scoped first (roles table above), then filtered; the
  subject role's `listReviews` silently narrows to `completed`.

### Skills catalog & employee skills

| Method & path | operationId | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/performance/skills` | `createSkill` | `SkillCreate` | `201 Skill` |
| `GET /api/v1/performance/skills` | `listSkills` | `?q=&include_archived=&page=&page_size=` | `200 {"items": Skill[], "page": Page}` |
| `PATCH /api/v1/performance/skills/{id}` | `updateSkill` | `SkillUpdate` | `200 Skill` |
| `POST /api/v1/performance/skills/{id}/archive` | `archiveSkill` | — | `200 Skill` |
| `POST /api/v1/performance/skills/{id}/restore` | `restoreSkill` | — | `200 Skill` |
| `POST /api/v1/performance/employees/{employee_id}/skills` | `recordEmployeeSkill` | `EmployeeSkillCreate` | `201 EmployeeSkill` |
| `GET /api/v1/performance/employees/{employee_id}/skills` | `listEmployeeSkills` | — | `200 {"items": EmployeeSkill[]}` |
| `PATCH /api/v1/performance/employees/{employee_id}/skills/{skill_id}` | `updateEmployeeSkill` | `EmployeeSkillUpdate` | `200 EmployeeSkill` |
| `DELETE /api/v1/performance/employees/{employee_id}/skills/{skill_id}` | `removeEmployeeSkill` | — | `204` |

`SkillCreate` / `SkillUpdate`: `name` (1..100), `description` (1..500).
`Skill`: `id`, `name`, `description`, `archived_at`. `EmployeeSkillCreate` /
`EmployeeSkillUpdate`: `skill_id` (create only), `proficiency` (1..5),
`note` (1..500). `EmployeeSkill`: `skill_id`, `skill_name`, `proficiency`,
`note`, `created_at`, `updated_at`.

Query semantics (pinned):

- `q` matches skill name case-insensitively (debounced on the web side);
  `include_archived=true` adds archived rows (default hides them).
- `archive`/`restore` are no-ops on an already-archived / already-active skill:
  `200`, no audit entry (ADR 0007).
- A foreign `skill_id` or `employee_id` anywhere in these routes → `404`
  (`skill_not_found` / `employee_not_found`), never `403`.

## Events

Eight v1 contracts under `contracts/events/performance/`, written before any
producer. Payloads carry **identifiers and state only** — no titles, comments,
notes, periods or ratings (PII rule, `.ai/security.md`). Every payload includes
`employee_id` (the subject), so the ADR 0012 outbox message key resolves
through its first fallback and **needs no amendment**.

| `event_type` | Emitted when | Payload sketch |
| --- | --- | --- |
| `performance.goal_created` | a goal enters `DRAFT` | `{goal_id, employee_id, status: "draft"}` |
| `performance.goal_activated` | `DRAFT ─► ACTIVE` | `{goal_id, employee_id, status: "active"}` |
| `performance.goal_completed` | `ACTIVE ─► COMPLETED` | `{goal_id, employee_id, status: "completed"}` |
| `performance.goal_cancelled` | `DRAFT|ACTIVE ─► CANCELLED` | `{goal_id, employee_id, status: "cancelled"}` |
| `performance.review_started` | a review enters `DRAFT` | `{review_id, employee_id, status: "draft"}` |
| `performance.review_completed` | `DRAFT ─► COMPLETED` (catalog, `.ai/domain.md`) | `{review_id, employee_id, status: "completed"}` |
| `performance.review_cancelled` | `DRAFT ─► CANCELLED` | `{review_id, employee_id, status: "cancelled"}` |
| `performance.feedback_submitted` | a nominated provider files feedback | `{feedback_id, review_id, employee_id}` |

Contract-before-producer holds even though nothing consumes these yet
(`recruitment.md` decision 5 precedent). Envelope rules:
`contracts/events/common/event-envelope.v1.json`; consumers dedupe on
`event_id` and ignore unknown fields.

## Edge cases

- **Terminated subject** (invariant 4): creating a goal or review for a
  terminated employee → `409 employee_not_active`; self-created goals likewise.
  Reading their existing goals/reviews/skills stays allowed (history is
  readable, like employment history).
- **Self-review**: a manager/HR actor whose `employee_id` equals the subject →
  `403` on create. HR can review the manager; the manager's own review is
  HR-authored.
- **Draft invisibility**: a subject who guesses a review id gets `404` (not a
  "denied" leak) for a review in `draft`; after completion the same id resolves.
  A nominated provider's `GET /reviews/{id}` follows the subject/author rules —
  a provider with no other standing sees the review shell (id, status) needed to
  file feedback, never the draft's rating or text.
- **Provider nomination churn**: removing a provider who already submitted
  feedback is rejected while the review is `draft` (`409
  feedback_already_submitted` for that provider — their submission stands);
  cancelling the review orphans the feedback (it stays readable to author + HR
  on the cancelled review, and no further submissions are accepted).
- **Archive semantics**: an archived skill keeps its name (uniqueness spans
  archived rows) so restore never conflicts; existing employee skills on it
  remain fully visible and editable, only new attach is blocked.
- **Progress sanity**: `PATCH` on a terminal (`completed|cancelled`) goal →
  `409 goal_terminal`; completing never auto-forwards `progress_pct`, so a
  completed goal may show less than 100.
- **Empty/duplicate inputs**: an empty `feedback_provider_ids` is valid (a
  review with no peer input); duplicate ids in the list are deduplicated; a
  provider who is also nominated on another review of the same subject is
  allowed (feedback is per review).
- **Rate/range violations** (`rating` 0 or 6, `proficiency` 0, oversized text)
  are `422` schema errors, never `500`; they follow the house validation shape.

## Acceptance criteria

- [x] API (goals): given an active employee, when they create a goal and
      activate it, then `201`/`200` reflect `draft → active`, the outbox holds
      `performance.goal_created` + `performance.goal_activated` rows in the
      same transaction, and exactly one audit entry per transition exists.
- [x] API (goals failure): given a `draft` goal, when `complete` is called, then
      `409 goal_not_active`; given an `active` goal, when `activate` is called
      again, then `409 goal_not_draft`; `PATCH` on a terminal goal →
      `409 goal_terminal`.
- [x] API (reviews): given a manager and their direct report, when the manager
      creates a review with a rating and completes it, then `200` shows
      `completed`, the outbox holds `performance.review_completed`, and the
      same review id now resolves for the subject (it did not before).
- [x] API (reviews failure): given a review without `rating`, when `complete`
      is called, then `409 review_incomplete`; given a `completed` review, when
      `PATCH` or `cancel` is called, then `409 review_not_draft`; given the
      actor's own employee id as subject, when `create` is called, then `403`.
- [x] API (feedback): given a nominated provider, when they submit a comment,
      then `201` and `performance.feedback_submitted` in the outbox; a second
      submission → `409 feedback_already_submitted`; a non-nominated employee →
      `403`; the subject calling `GET …/feedback` → `403 feedback_access_denied`.
- [x] API (skills): given HR creates a skill and the employee attaches it with
      a proficiency, then `201` + `201`; a duplicate skill name (including an
      archived row) → `409 skill_name_taken`; attaching an archived skill →
      `409 skill_archived`; attaching the same skill twice →
      `409 employee_skill_exists`.
- [x] API (denied role): given an Employee, when they create a review for a
      colleague or call any skill-catalog mutation, then `403`; given a Finance
      user, when they manage goals or reviews, then `403`.
- [x] API (tenant isolation): given goals, reviews and skills of tenant A, when
      tenant B lists, reads by id, or passes them as nested `employee_id` /
      `skill_id` / `feedback_provider_ids`, then `404` (never `403`).
- [x] API (audit & PII): every mutating route writes exactly one append-only
      audit entry whose `changes` carry ids and state only — asserting no
      `title`, `strengths`, `improvements`, `comment`, `note` or `rating`
      value appears in audit rows, logs or event payloads.
- [x] Events (contract): each of the eight payloads validates against
      `contracts/events/performance/*`, carries `employee_id`, and contains
      identifiers + state only.
- [x] Web (goals): given `/performance`, when the user creates, activates and
      completes a goal (with `progress_pct`), then the list reflects every
      state without a reload, and the Vietnamese locale renders the same flow
      from the `performance` catalog (no hardcoded English).
- [x] Web (reviews): given a manager, when the review completes, then the
      subject's "My reviews" shows it with rating and sections; while it is
      `draft` the subject's surface shows no trace of it, and no surface ever
      shows feedback text to the subject.
- [x] Web (feedback): given a nominated provider, when they open the
      feedback worklist, they can submit exactly once and a second attempt is
      refused with the pinned error copy.
- [x] Web (failure): given a failed mutation (`409 goal_not_active`,
      `409 feedback_already_submitted`), when it happens, then the pinned
      error message surfaces (toast/inline) and the form keeps the user's
      input.
- [x] Web (denied): given an Employee role, when `/performance` renders, then
      team tabs, review creation and catalog management are absent, and the
      denied state appears for team-scoped actions.
- [x] Tests & gates: unit coverage of both state machines and the policy
      matrix (including every denied-role case), integration coverage of the
      routes with tenant isolation, contract coverage of API shape + the eight
      event payloads, Vitest coverage of the feature, and `tests/e2e/performance.spec.ts`
      proving manager creates review → nominates peer → peer files feedback →
      manager completes → the employee sees the completed review, plus the
      failure paths (duplicate feedback, completing without a rating) and the
      hidden/denied employee view. `make check` green.

## Decisions (PO, 2026-10-26)

1. **Ad-hoc manager reviews** — a review is started any time for any team
   member, with an optional `period` label. No HR review cycles, windows or
   assignments in v1. Rationale: cycles double the entity count for a first
   Performance slice; they can be added later without breaking the review
   contract.
2. **Manager-only review, employee sees the result** — the manager (or HR)
   writes and completes the review; the subject sees their completed reviews.
   No self-assessment step, no acknowledgement/sign-off. Rationale: keeps the
   state machine at `draft → completed | cancelled` while still giving the
   employee the outcome.
3. **Feedback is tied to reviews** — peer input is nominated by and filed onto
   a review, visible to the review author and HR only; the subject never sees
   it. Standalone kudos-style feedback is rejected. Rationale: feedback stays
   an input to a judgment instead of a social surface with its own PII
   exposure.
4. **Goals: self + manager authorship, simple lifecycle** — employees own
   their goals, managers own their team's, HR anyone's;
   `draft → active → completed | cancelled` with due dates and `progress_pct`.
   No OKR/parent-child alignment. Rationale: ownership where the work happens,
   no tree machinery.
5. **Skills: tenant catalog + proficiency** — HR/Admin manage a per-tenant
   catalog (ADR 0007 semantics), anyone with standing records an employee's
   skill with proficiency 1–5. No endorsements. Rationale: a claim + level is
   the useful minimum; endorsements add social policy edge cases.

Design details fixed by this spec (not product scope, following the answers and
house precedent): feedback arrives through nominated `feedback_provider_ids`
(no separate request entity); the eight-event set in `## Events` extends the
`.ai/domain.md` catalog additively; skill archive blocks only new assignment,
not history (the one documented ADR 0007 divergence); rating is integer 1..5.

## Plan

Planned 2026-10-26. Order keeps `make check` green after every task: contracts →
data → builders/policies → goals → reviews/feedback → skills → audit sweep →
web foundation → web tabs → e2e → record. Backend lands before frontend
against the pinned contract; the eight event contracts land before any
producer (rule 4).

### Acceptance criteria → change map

| # | Acceptance criterion | Satisfied by |
| --- | --- | --- |
| 1 | API goals happy path (201/200, outbox + audit per transition) | T3 builders · T5 goal service/routes |
| 2 | API goals failures (`goal_not_active`, `goal_not_draft`, `goal_terminal`) | T5 (conditional-update transitions + terminal PATCH guard) |
| 3 | API reviews happy path (`review_completed` outbox; subject id resolves only after) | T6 |
| 4 | API reviews failures (`review_incomplete`, `review_not_draft`, self-review `403`) | T6 |
| 5 | API feedback (201 + event, double-submit 409, non-nominated 403, subject `feedback_access_denied`) | T6 |
| 6 | API skills (`skill_name_taken`, `skill_archived`, `employee_skill_exists`) | T7 |
| 7 | API denied roles (Employee→review/catalog `403`, Finance→goals/reviews `403`) | T4 policy matrix + T5–T7 tests |
| 8 | API tenant isolation (`404` on list/read/nested ids) | T5–T7 integration tests |
| 9 | API audit & PII (one entry per mutation, ids+state only) | T5–T7 audit writes · T8 sweep |
| 10 | Events contract (8 payloads, `employee_id`, ids+state only) | T1 schemas · T3 contract tests |
| 11 | Web goals (create/activate/complete + `progress_pct`, no reload, vi locale) | T9 catalog · T10 goals tab |
| 12 | Web reviews (subject's completed-only "My reviews"; draft invisible; feedback never shown to subject) | T11 |
| 13 | Web feedback (worklist, submit exactly once, pinned refusal copy) | T11 |
| 14 | Web failure (pinned error message, form keeps input) | T9 `labels.ts` error map · T10–T12 forms |
| 15 | Web denied (Employee: no team tabs/review create/catalog; denied state) | T9 `roles.ts` + workspace gating |
| 16 | Tests & gates (`make check` green incl. e2e hop) | every task's verify step · T13 · T14 |

### Design pins (mechanics the spec leaves to house precedent)

- Error body is the house shape `{"error": {"code", "message"}}`; one
  `WorkPulseError` subclass per code in `domains/performance/exceptions.py`.
  Codes: pinned ones from the spec (`goal_not_draft`, `goal_not_active`,
  `goal_terminal`, `review_incomplete`, `review_not_draft`,
  `feedback_provider_invalid`, `feedback_already_submitted`,
  `feedback_access_denied`, `skill_name_taken`, `skill_archived`,
  `employee_skill_exists`, `employee_not_active`) plus `goal_not_found`,
  `review_not_found`, `skill_not_found`, `employee_not_found` (404, foreign and
  unknown collapse into one code) and a generic `403 performance_denied`
  (recruitment's `recruitment_denied` precedent) for role denials and
  self-review.
- A subject resolving a **draft** review id gets `404 review_not_found` (AC 3
  "it did not before"), never a denied leak.
- A nominated provider with no other standing gets the **shell** serialization
  of `Review`: `rating`/`period`/`strengths`/`improvements` null and
  `feedback_provider_ids` omitted (schema fields are nullable to allow this).
- `feedback_for_me=true` combined with `status` other than `draft` → `422
  {"error": {"code": "validation_error"}}` ("cannot be combined").
- `listReviews` for the Employee role silently narrows to `completed` even
  when `status` is passed (pinned query semantics).
- `GoalCreate.employee_id` omitted and the actor has no `employee_id` link →
  fail closed `403 performance_denied` (overtime's missing-link precedent).
- `reviews.author_id` stores the actor **user** id (ADR 0010 — HR/Admin seeds
  have no employee link); `review_feedback.author_id` stores the provider's
  **employee** id so it matches a `feedback_provider_ids` nomination.
- Nominations live in an association table `performance_review_providers`
  (`review_id`, `employee_id`, both PK parts, `tenant_id` on the row) so
  `feedback_for_me` and "removing a provider who submitted" are joins, not
  JSON scans; wire schema still exposes `feedback_provider_ids`.
- Tables: `performance_goals`, `performance_reviews`,
  `performance_review_providers`, `performance_review_feedback`,
  `performance_skills`, `performance_employee_skills` (unique
  `(employee_id, skill_id)`, no surrogate id — `EmployeeSkill` is keyed by
  `skill_id` in the route). `tenant_id` on every row (invariant 1).
- `archiveSkill`/`restoreSkill` take **no body** (the spec table wins over the
  organizations `ArchiveRequest {reason}` precedent).
- Progress updates (`progress_pct`, title, description while `draft|active`)
  emit no event but write one `goal.updated` audit entry with
  PII fields as the `"changed"` marker.

### Ordered tasks

**T1 — Event contracts (before any producer).**
Add `contracts/events/performance/performance-{goal-created,goal-activated,
goal-completed,goal-cancelled,review-started,review-completed,review-cancelled,
feedback-submitted}.v1.json` — JSON Schema draft 2020-12, the file's
`<event-domain>-<event>` prefix matching `performance.<snake_case>` event_type
(the repo's contract-sweep convention), `additionalProperties: false`, every
property required, payloads exactly the spec `## Events` table (ids + status
only; `feedback_submitted` = `{feedback_id, review_id, employee_id}`).
Copy the shape of `contracts/events/recruitment/candidate-created.v1.json`.
Verify: `make check-api` still green (nothing consumes them yet); each file
loads under `Draft202012Validator.check_schema`.

**T2 — Domain data + wire foundation.**
`apps/api/app/domains/performance/{__init__.py, models.py, exceptions.py,
schemas.py}`: SQLAlchemy models for the six tables with `CheckConstraint`s on
`status`/`rating`/`progress_pct`/`proficiency`, unique
`(tenant_id, name)` spanning archived skills (ADR 0007), unique
`(review_id, author_id)` on feedback, list/filter indexes mirroring
`0014_overtime.py` conventions; Pydantic v2 schemas (`extra="forbid"` writes,
`from_attributes` reads, lowercase `StrEnum` statuses, `date` for `due_date`,
nullable fields for the provider shell) incl. `GoalListPage`/`ReviewListPage`/
`SkillListPage` (`{"items", "page"}` via `shared/pagination.py`). Migration
`apps/api/alembic/versions/0018_performance.py` (append-only, named
constraints via `op.f`); any partial/covering index declared in **both** the
migration and `__table_args__` (parity rule). Append the six tables to
`ALL_TABLES` in `apps/api/tests/integration/conftest.py`.
Verify: `make db-migrate` + `make check-api` (ruff/pyright/pytest) green.

**T3 — Event builders + contract tests.**
`apps/api/app/domains/performance/events.py`: `EventName` Literal + `_envelope()`
(recruitment's builder shape, `evt_<uuid7>` ids) and eight pure builders
returning the pinned payload dicts. `apps/api/tests/contract/
test_performance_events.py`: envelope + payload validation per schema, PII key
sweep (`_PII_KEY_MARKERS` incl. `rating`), schema-rejects-tampering
(`additionalProperties: false` proof), required-fields, filename ↔
`event_type` mapping, `EventEnvelope.model_validate` outbox gate, and
`kafka_message_key` order (`employee_id` first — no ADR 0012 amendment).
Verify: `make check-api` green with the new contract tests.

**T4 — Policies + repositories (ports + adapters).**
`apps/api/app/domains/performance/policies.py`: `ensure_can_manage_goals`,
`ensure_can_run_reviews`, `ensure_can_nominate`, `ensure_can_manage_skills`,
`ensure_can_record_skills`, `ensure_can_view_feedback` implementing the
`## Roles & access` matrix (Employee/Manager/HR/Finance/Admin/Super Admin) and
the never-self rule. `apps/api/app/domains/performance/repository.py`:
`Protocol`s speaking wire schemas + filter dataclasses + an `EmployeeScope`
port (same shape as `app/domains/reports/ports.py`; reused adapter
`app/infrastructure/postgres/manager_scope.py` `PostgresEmployeeScope` =
self + direct reports). `apps/api/app/infrastructure/postgres/repositories/
performance.py`: `PostgresPerformanceRepository` with conditional-UPDATE
transition methods returning `None` on a lost race (overtime
`transition_approve` precedent). Unit tests
`apps/api/tests/unit/performance/{__init__.py, conftest.py, fakes.py,
test_policies.py}` (pinned `NOW`, `identity()` helper, `Fake*Repo`,
`RecordingAuditWriter`, `RecordingEventOutbox`).
Verify: `make check-api`; policy tests cover every denied-role cell.

**T5 — Goals flow.**
`apps/api/app/domains/performance/service.py` `GoalService` (create → draft +
`goal_created`; conditional transitions `activate`/`complete`/`cancel` with
`goal_*` events + one audit entry each; `update` while `draft|active` only,
`409 goal_terminal` otherwise; list visibility-scoped via `EmployeeScope`);
`apps/api/app/api/v1/performance.py` goals routes with the pinned
operationIds; register in `apps/api/app/api/v1/__init__.py`; wire
`GoalService` in `apps/api/app/dependencies.py`
(`PostgresPerformanceRepository(session)` + `PostgresAuditWriter(session)` +
`PostgresEventOutbox(session, clock=_utc_now)` — same transaction). Unit tests
`tests/unit/performance/test_goals_service.py` (state machine + audit +
events + no-event-on-progress). Integration
`tests/integration/test_performance_goals.py`: AC 1–2, tenant isolation 404,
denied roles (Employee→foreign goal, Finance→any goal mutation), outbox row
order/stamping via `EventOutboxRow`, audit rows with `"changed"` markers.
Run `make gen-api` and commit `contracts/openapi/openapi.json` +
`packages/api-client/` with the routes (the openapi drift contract test
compares `app.openapi()` to the checked-in file).
Verify: `make check-api`.

**T6 — Reviews + feedback flow.**
`ReviewService` in `service.py`: create (`403` self-review, `409
employee_not_active`, provider validation `409 feedback_provider_invalid`,
dedupe, ≤10) + `review_started`; draft-only update incl. provider-set churn
(`409 feedback_already_submitted` when removing a provider who filed);
`complete` (needs `rating`, `409 review_incomplete`, stamps `completed_at`,
`review_completed`) and `cancel` (`review_cancelled`); feedback submit
(`409 review_not_draft` / `409 feedback_already_submitted` / `403` non-nominated,
`feedback_submitted`, immutable); visibility matrix incl. subject = completed
only, draft-id 404, provider shell, `GET …/feedback` `403
feedback_access_denied` for the subject. Reviews + feedback routes in
`api/v1/performance.py` (pinned operationIds incl. `submitReviewFeedback`,
`listReviewFeedback`), dependencies wiring, `make gen-api`. Unit
`tests/unit/performance/test_reviews_service.py`; integration
`tests/integration/test_performance_reviews.py` covering AC 3–5, tenant
isolation through nested `employee_id`/`feedback_provider_ids`, provider
churn + cancelled-review orphaned feedback.
Verify: `make check-api`.

**T7 — Skills catalog + employee skills.**
`SkillService` (catalog create/rename/archive/restore — no-op repeats return
`200` with no audit entry (ADR 0007), name uniqueness spans archived → `409
skill_name_taken`, no in-use block (the documented divergence), `q` +
`include_archived` listings; employee skills attach `409 skill_archived` /
`409 employee_skill_exists`, in-place update, audited remove, no events).
Routes for the nine skill operations (pinned operationIds), dependencies,
`make gen-api`. Unit `tests/unit/performance/test_skills_service.py`;
integration `tests/integration/test_performance_skills.py` covering AC 6,
foreign `skill_id`/`employee_id` → 404, Employee-role catalog `403`.
Verify: `make check-api`.

**T8 — Cross-cutting audit & PII sweep.**
`tests/integration/test_performance_audit.py`: drive every mutating route and
assert exactly one `AuditEntry` per mutation whose `changes` carry ids +
state + `"changed"` markers only — string-sweep the rendered audit rows,
outbox payloads and caplog output for the concrete PII values
(`title`, `description`, `strengths`, `improvements`, `comment`, `note`,
`rating` values). Verify: `make check-api && make check-web` (generated client
typechecks).

**T9 — Web foundation.**
`apps/web/messages/{en,vi}/performance.json` (namespaces `nav`-adjacent:
`tabs`, `goals`, `reviews`, `feedback`, `skills`, `toasts`, `errors`,
`validation`) merged in `messages/{en,vi}/index.ts`; nav key in
`messages/{en,vi}/nav.json`; `features/performance/{api.ts (query keys +
invalidate*), types.ts, labels.ts (status badges + `performanceErrorMessage`
ApiError.code → copy switch), roles.ts (mirror sets: manage/review/catalog/
record/feedback capability rows), dates.ts}`; `hooks/use-performance.ts`
(named mutation hooks: invalidate + toast, field-error mappers); route shell
`app/(dashboard)/performance/page.tsx` (server component, `getTranslations`)
rendering `features/performance/components/PerformanceWorkspace.tsx` with
role-derived tabs Goals · Reviews · Skills (recruitment's
`RecruitmentWorkspace` pattern) and the Employee-role denied state; `NAV_ITEMS`
entry + `NavKey` union in `components/destinations.ts`.
Vitest `apps/web/tests/performance/workspace.test.tsx` (tab visibility per
role, denied state, catalog parity via existing `tests/i18n/catalog.test.ts`).
Verify: `make check-web`.

**T10 — Web goals tab.**
`features/performance/components/` goals table + `GoalFormDialog` +
activate/complete/cancel actions + `progress_pct` editing; `schema.ts`
`goalFormSchema` (translated validation copy, mirrors the pinned contract);
hooks `useGoalCreate`/`useGoalUpdate`/`useGoalActivate`/… with invalidation so
state flips without a reload (mutation invalidation baseline; overtime-style
`refetchInterval` polling only if cross-user staleness shows up in e2e).
Vitest `apps/web/tests/performance/goals.test.tsx` (happy path, `409
goal_not_active` refusal keeps form input with pinned copy, pagination clamp
handling, vi catalog render). Verify: `make check-web`.

**T11 — Web reviews + feedback.**
Author workspace: review table (`?employee_id=&status=`), `ReviewFormDialog`
(create with provider nomination chips ≤10; draft update incl. rating
1–5 select), complete/cancel actions, feedback panel (author + HR see all
feedback rows). Subject "My reviews" surface: completed-only list, rating +
strengths/improvements; **no feedback surface at all** for the subject.
Provider worklist: `feedback_for_me=true` view + one-shot `FeedbackDialog`
(second attempt refused with the pinned `feedback_already_submitted` copy).
Vitest `apps/web/tests/performance/{reviews,feedback}.test.tsx` (subject
draft-invisibility, provider shell rendering, submit-once refusal, no feedback
text anywhere in the subject tree). Verify: `make check-web`.

**T12 — Web skills tab.**
Catalog table (create/rename dialog, archive/restore actions, `q` debounced
via `@/hooks/use-debounced-value`, `include_archived` toggle, archived rows
badged and absent from pickers) + employee-skills panel (attach from active
picker, proficiency 1–5, note, update/remove) with standing scoped like the
goals tab. Vitest `apps/web/tests/performance/skills.test.tsx` (`409
skill_name_taken` / `skill_archived` / `employee_skill_exists` copy, archived
picker exclusion). Verify: `make check-web`.

**T13 — E2E + fixtures.**
`apps/api/scripts/reset_performance_fixtures.py` (idempotent reset of
performance rows **and** creation of a peer employee not subject/author — the
dev seeds only have Wendy + Morgan, so nomination needs a third employee — plus
its linked `peer@workpulse.test` login, because feedback can only be filed by a
nominated provider's own session);
register it in `tests/e2e/full-hop.sh` alongside the other resets;
`tests/e2e/performance.spec.ts` (`test.describe.serial`, `uiLogin` personas
manager@ / worker@ / the seeded peer): manager creates review → nominates peer
→ peer files feedback → manager completes → worker's "My reviews" shows it;
failure paths: duplicate feedback refused, complete-without-rating refused,
draft review invisible to the worker, Employee-role denied view.
Verify: `make check-e2e`, then full `make check`.

**T14 — Record.**
Tick `## Acceptance criteria`, update `tasks/TODO.md`, add the capability rows
to `.ai/security.md`'s roles matrix, extend `.ai/domain.md`'s event catalog
additively with the eight events, and add ADR 0021 for the ADR 0007 divergence
(see below). Docs-only; final `make check` green.

### Risks, unknowns, ADR

- **ADR 0021 (recommended)** — "skill archive is never blocked by use": the one
  deliberate ADR 0007 divergence. **Landed** at T14
  (`.ai/decisions/0021-skill-archive-not-blocked-by-use.md`); everything else
  follows existing decisions (0007 soft delete, 0010 identity, 0012 outbox, 0015
  trigger map untouched, recruitment decision 5 events-only precedent). It is
  the one behavior a future reader will otherwise "fix" back into parity with
  departments.
- **e2e persona gap** — seeds have only two employees (Wendy ↔ Morgan), but the
  feedback flow needs a third (neither subject nor author). T13's reset script
  creates one; if a permanent persona is preferred instead, add it to
  `database/postgres/seeds/003_users.sql` (a decision for T13).
- **Provider shell serialization** — nullable `rating`/`period`/`strengths`/
  `improvements` widen the orval types; the web must treat them as absent
  rather than `0`/empty. Contract tests pin the shape.
- **`feedback_for_me` + `status`** — the spec says "cannot be combined"; the
  plan pins `422 validation_error`. Cheap to change if product wants silent
  narrowing instead.
- **Churn races** — provider removal vs. concurrent submission, and
  complete-vs-feedback races, rely on conditional updates + unique
  `(review_id, author_id)`; unit fakes need a `lose_next_transition` flag
  (recruitment precedent) to prove the lost-race 409 path.
- **Notifications/analytics follow-ups** — deliberately untouched (ADR 0015
  trigger map, no ClickHouse projection); the eight events are contract-only
  for now and must not be "helpfully" consumed here.

### Test strategy

| Gate | What it covers | Failure cases pinned |
| --- | --- | --- |
| `make check-api` — unit (`tests/unit/performance/`) | both state machines (goal, review), policy matrix incl. every denied-role cell, skill archive semantics, provider churn, audit/event emission via recording fakes | `goal_not_draft`, `goal_not_active`, `goal_terminal`, `review_incomplete`, `review_not_draft`, `feedback_already_submitted`, `feedback_provider_invalid`, `skill_name_taken`, `skill_archived`, `employee_skill_exists`, `employee_not_active`, all `403 performance_denied` cells |
| `make check-api` — integration (`tests/integration/test_performance_*.py`) | route flows with real Postgres: AC 1–9, outbox rows same-transaction + stamped, one audit row per mutation, PII sweep (T8), tenant isolation, subject draft-404, provider shell, `feedback_access_denied` | duplicate feedback, completing without rating, foreign ids → 404, Employee/Finance denials, terminal PATCH |
| `make check-api` — contract (`tests/contract/test_performance_events.py` + `test_openapi_drift.py`) | AC 10: eight payloads vs schemas + envelope, ids+state only, PII key rejection, message-key order; API shape = checked-in OpenAPI | tampered payload with a PII key, dropped required field, drift after route edits (fixed by `make gen-api` in T5–T7) |
| `make check-web` — Vitest (`apps/web/tests/performance/`) | AC 11–15: workspace tabs per role, goals flow, review/feedback surfaces, submit-once, pinned error copy with input preserved, catalog parity (en/vi) | `409` mapping, denied state, subject tree contains no feedback text |
| `make check-e2e` (`tests/e2e/performance.spec.ts`) | AC 16 hop: manager → peer feedback → complete → employee sees it; duplicate feedback refused; complete-without-rating refused; draft hidden; Employee denied | full `make check` must be green at T14 |
