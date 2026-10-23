# Feature: Recruitment

- **Status:** approved
- **Phase:** 4+ (`.ai/domain.md` context map; first feature after the Phase-4 core)
- **Size:** L
- **Surfaces:** api+web+events
- **Links:** `.ai/domain.md` (Recruitment: Job, Candidate, Interview; event catalog rows `candidate.created` / `interview.completed`) · `.ai/product.md` (HR "runs recruitment"; decision 4 defers documents) · `.ai/security.md` (roles matrix, PII) · ADR 0014 (seam pattern — hire → Employees) · ADR 0010 (actor ids) · ADR 0012 (outbox) · `employees.md` (the hire seam target: `CreateEmployee`, `employee.created`) · `overtime.md` (the analogous workflow-style feature) · `tasks/doc.md` §1 (Jobs · Candidates · Interviews · Hiring)

## Problem

Hiring lives outside WorkPulse: jobs are posted somewhere else, candidates sit in
spreadsheets or inbox threads, interviews happen without a record, and the hire
lands in Employees by manual re-entry. There is no pipeline a manager and HR can
share, no audit trail for who advanced or rejected a candidate, and no event
stream for the "recruitment moves" the domain catalog promises. This feature
gives recruitment a real home: jobs → candidates → interviews → hiring, ending
in an Employee record created through the Employees seam — one continuous,
audited flow.

## Business rules

- **Candidate state machine** (new — extends `.ai/domain.md` the way `leave.md`
  and `overtime.md` extended theirs; every transition is an event + an audit
  entry):

  ```text
  APPLIED ─► INTERVIEWING ─► OFFERED ─► HIRED (terminal)
     │              │             │
     └──────────────┴─────────────┴─► REJECTED | WITHDRAWN (terminal)
  ```

  Stage moves are **HR-driven** (HR / Admin / Super Admin): `advance` steps one
  stage forward (`applied → interviewing`, `interviewing → offered`); `reject`
  and `withdraw` are allowed from any pre-hired state; `hire` only from
  `OFFERED`. There is no re-open of a terminal candidate — a comeback is a new
  candidate row. `WITHDRAWN` is recorded by HR (the candidate pulled out);
  candidates are external people, not users, so nothing is self-service.
- **Job state machine** (new):

  ```text
  DRAFT ─► OPEN ─► CLOSED (terminal)
  ```

  Created as `DRAFT`; `open` makes it accept candidates; `close` is manual and
  terminal. Jobs link to the Organization context (`department_id`,
  `position_id`, `location_id` — the same references `employees.md` uses) and
  carry `openings` (int ≥ 1): hiring is blocked with `409 job_openings_filled`
  once hired candidates reach `openings` (first hire wins a contested opening).
  Closing a job with candidates in flight is allowed; open candidate workflows
  continue, but `create candidate`/`hire` on a non-`OPEN` job is refused
  (`409 job_not_open`).
- **Intake is HR-entered only** (PO Decision 1): HR/Admin create candidate rows
  (referrals, job boards, agencies). No public application surface in v1.
  A candidate is an **application on one job**: fields `first_name`,
  `last_name`, `email`, `phone?`, `source`
  (`referral|job_board|agency|other`), `notes?` (1..2000, PII). One row per
  `(tenant, job_id, email)` → duplicate `409 candidate_already_applied`.
- **Interviews** belong to a candidate and are only schedulable while the
  candidate is `INTERVIEWING` (`409 candidate_stage_conflict` otherwise).
  Scheduling names an `interviewer_id` (an employee uuid — typically a manager;
  any active employee may be assigned) plus `scheduled_at` (UTC) and optional
  `notes?`. States: `SCHEDULED ─┬─► COMPLETED`, `└─► CANCELLED` — both terminal.
  `COMPLETED` happens exactly when the assigned interviewer submits feedback
  (`rating` int 1..5 required, `notes?` 1..1000); that transition emits
  `interview.completed` (the catalog event = feedback filed). Only the assigned
  interviewer submits feedback; HR may `update` (reschedule / re-assign) or
  `cancel` while `SCHEDULED`. A terminated employee cannot be assigned
  (`409 interviewer_not_active`).
- **Hiring creates the employee** (PO Decision 2) through the Employees seam
  (ADR 0014 pattern): `hire` collects `employment_type`, `hired_at` and
  optional `manager_id`, `employee_code`, and name/email/phone **overrides**
  (defaulting to the candidate's record); `department_id`/`position_id`/
  `location_id` come from the job. The employee is created via the Employees
  service through a port — never its tables — in the same transaction as the
  `HIRED` transition. The seam's own errors propagate unchanged
  (`employee_email_taken`, `employee_code_taken`, `employee_manager_invalid`,
  `department_not_found`, `position_not_found`, `location_not_found`) and fail
  the hire atomically: the candidate stays `OFFERED`. `employee.created` is
  emitted by Employees as usual; recruitment emits `candidate.hired`.
- **Access is HR-run** (security matrix row *Run recruitment* — added to
  `.ai/security.md` in this change): jobs, candidates, pipeline moves,
  scheduling, and hiring are HR / Admin / Super Admin only. An assigned
  interviewer (any role) gets a **scoped** view: their own interviews
  (`?mine=1`), the candidate behind them (context for the conversation), and
  feedback on their own interviews — nothing else. Managers with no assignment
  see nothing (`404`). Roles with no standing at all (Employee, Finance) are
  refused at the endpoint (`403`).
- **PII** (`.ai/security.md`): candidate names, `email`, `phone`, `notes`,
  interview `notes`, feedback `notes` and rejection reasons are sensitive —
  never in logs, event payloads, error messages or audit `changes` values
  (recorded as "field changed"). Events carry identifiers, state and
  non-personal counts only. Candidate rows are as protected as employee rows.
- Tenant isolation (invariant 1), policies in
  `domains/recruitment/policies.py` (AGENTS.md rule 12), time-as-a-parameter
  (`.ai/conventions.md`) and cross-tenant `404` hold as everywhere else.

## Scope

- Jobs: create (`DRAFT`), edit while `DRAFT`/`OPEN`, open, close, list/detail
  with filters (`status`, `q`, `department_id`, pagination).
- Candidates: create on an open job, edit contact fields, list/detail with
  filters (`job_id`, `status`, `q`), advance / reject / withdraw, hire.
- Interviews: schedule on an `INTERVIEWING` candidate, update (reschedule /
  re-assign) or cancel while `SCHEDULED`, complete with feedback; a `mine`
  listing for interviewers.
- Event contracts written **first** under `contracts/events/recruitment/`
  (extending the `.ai/domain.md` catalog — the same change updates it):
  `job.opened`, `job.closed`, `candidate.created`, `candidate.advanced`,
  `candidate.rejected`, `candidate.withdrawn`, `candidate.hired`,
  `interview.scheduled`, `interview.completed`, `interview.cancelled`.
  Produced through the `EventOutbox` port in the same transaction as the
  transition (ADR 0012).
- The hire → Employees seam port + adapter (in-process, same transaction), with
  an atomicity test (seam failure leaves the candidate `OFFERED`).
- Web: a `/recruitment` workspace — Jobs, Candidates and Interviews management
  tabs for HR/Admin, and a "My interviews" tab for assigned interviewers
  (Manager/HR/Admin/Super Admin nav; Employee/Finance never see the nav).

## Out of scope

- **Candidate documents / CV files** (PO Decision 3): fields only. Presigned
  upload/download waits for the Phase 4+ security review (product decision 4).
- **Public application / careers page** (PO Decision 1): intake is HR-entered;
  an unauthenticated apply surface (abuse control, spam, public PII intake) is
  a separate feature.
- **Notification triggers and analytics** (PO Decision 5): events are published
  and consumed later; ADR 0015's trigger map is untouched here and the
  ClickHouse projection / reports gain no recruitment inputs (time-to-hire and
  funnel reports are a follow-up on top of the events landed here).
- Offer-letter generation, compensation negotiation, salary offers, approvals
  chains on offers.
- Interview scheduling aids: calendar conflict detection, slot booking,
  reminders, video links, multi-round templates (rounds are just multiple
  interview rows).
- Bulk candidate import/CSV, talent-pool/evergreen candidates (a candidate
  always belongs to one job), re-opening terminal candidates, candidate
  duplication merge.
- Job approval workflows (a manager "requesting" a job — jobs are created by
  HR), job-board integrations, referral tracking beyond the `source` field.
- Onboarding workflows after hire (the employee record is the hand-off).

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/recruitment/` (models · schemas · service · repository · events · policies · exceptions) |
| API router | `apps/api/app/api/v1/recruitment.py` |
| Seam adapter | `apps/api/app/infrastructure/postgres/` (`EmployeeHirePort` impl; wired in `app/dependencies.py`) |
| Events | `contracts/events/recruitment/` (`job-opened` · `job-closed` · `candidate-created` · `candidate-advanced` · `candidate-rejected` · `candidate-withdrawn` · `candidate-hired` · `interview-scheduled` · `interview-completed` · `interview-cancelled`, v1) |
| Workers | — (outbox relay publishes; no recruitment worker) |
| Web feature | `apps/web/features/recruitment/` |
| Web routes | `apps/web/app/(dashboard)/recruitment/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/recruitment.spec.ts` |

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Manage jobs (create/edit/open/close) | — | — | Y | — | Y | Y |
| Manage candidates (create/edit/advance/reject/withdraw) | — | — | Y | — | Y | Y |
| Hire a candidate | — | — | Y | — | Y | Y |
| Schedule / update / cancel interviews | — | — | Y | — | Y | Y |
| View jobs & candidates | — | assigned interviews only | Y | — | Y | Y |
| List own interviews (`?mine=1`) | own | own | own | own | own | own |
| Submit feedback on own interview | own | own | own | own | own | own |

Roles with no standing (Employee, Finance on management endpoints) get `403`.
Cross-tenant and out-of-scope entity ids get `404` (existence is not leaked): a
Manager reading a candidate they are not assigned to, or completing another
interviewer's interview, sees `404`. A viewer with standing attempting a
forbidden transition gets `403`; a wrong-state transition gets `409`.

## API contract (pinned before code)

| Method & path | operationId (orval hook) | Input | Response |
| --- | --- | --- | --- |
| `POST /api/v1/recruitment/jobs` | `createJob` | `JobCreate` | `201 Job` (`draft`) |
| `GET /api/v1/recruitment/jobs` | `listJobs` | `?status=&q=&department_id=&page=&page_size=` | `200 {"items": Job[], "page": Page}` |
| `GET /api/v1/recruitment/jobs/{job_id}` | `getJob` | — | `200 Job` |
| `PATCH /api/v1/recruitment/jobs/{job_id}` | `updateJob` | `JobUpdate` | `200 Job` (only while `draft`/`open`) |
| `POST /api/v1/recruitment/jobs/{job_id}/open` | `openJob` | — | `200 Job` |
| `POST /api/v1/recruitment/jobs/{job_id}/close` | `closeJob` | — | `200 Job` |
| `POST /api/v1/recruitment/jobs/{job_id}/candidates` | `createCandidate` | `CandidateCreate` | `201 Candidate` (`applied`) |
| `GET /api/v1/recruitment/candidates` | `listCandidates` | `?job_id=&status=&q=&page=&page_size=` | `200 {"items": Candidate[], "page": Page}` |
| `GET /api/v1/recruitment/candidates/{candidate_id}` | `getCandidate` | — | `200 Candidate` |
| `PATCH /api/v1/recruitment/candidates/{candidate_id}` | `updateCandidate` | `CandidateUpdate` | `200 Candidate` |
| `POST /api/v1/recruitment/candidates/{candidate_id}/advance` | `advanceCandidate` | — | `200 Candidate` |
| `POST /api/v1/recruitment/candidates/{candidate_id}/reject` | `rejectCandidate` | `CandidateDecision` | `200 Candidate` |
| `POST /api/v1/recruitment/candidates/{candidate_id}/withdraw` | `withdrawCandidate` | `CandidateDecision` | `200 Candidate` |
| `POST /api/v1/recruitment/candidates/{candidate_id}/hire` | `hireCandidate` | `HireCandidate` | `200 CandidateDetail` (with `employee_id`) |
| `POST /api/v1/recruitment/candidates/{candidate_id}/interviews` | `scheduleInterview` | `InterviewSchedule` | `201 Interview` (`scheduled`) |
| `GET /api/v1/recruitment/interviews` | `listInterviews` | `?candidate_id=&job_id=&status=&mine=&from=&to=&page=&page_size=` | `200 {"items": Interview[], "page": Page}` |
| `PATCH /api/v1/recruitment/interviews/{interview_id}` | `updateInterview` | `InterviewUpdate` | `200 Interview` (only while `scheduled`) |
| `POST /api/v1/recruitment/interviews/{interview_id}/complete` | `completeInterview` | `InterviewFeedback` | `200 Interview` |
| `POST /api/v1/recruitment/interviews/{interview_id}/cancel` | `cancelInterview` | `CandidateDecision` | `200 Interview` |

The operationIds above are the orval hook names and are pinned: the router sets
them as `operation_id` and the generated `@workpulse/api-client` hooks must
match exactly.

Query semantics (pinned):

- `status` accepts one lowercase value: jobs
  (`draft|open|closed`), candidates
  (`applied|interviewing|offered|hired|rejected|withdrawn`), interviews
  (`scheduled|completed|cancelled`). JSON `status` values are lowercase
  everywhere.
- `q` matches job title or candidate first/last name (case-insensitive
  substring); the search input is debounced in the web per `.ai/conventions.md`.
- `mine=1` (truthy) restricts interviews to those whose `interviewer_id` is the
  caller's `identity.employee_id`. Callers without management standing are
  always forced to that scope (whether or not `mine` is passed) and cannot use
  the `interviewer_id` filter; management viewers may filter by `interviewer_id`.
  `from`/`to` filter `scheduled_at` inclusively.
- Pagination is the house `PageParams` clamp shape (clamp, never an error).
- Foreign (other-tenant / unknown) filter ids → `404` with the entity's code
  (`job_not_found`, `candidate_not_found`, `interviewer_not_found`).

Transition semantics (pinned):

- `advance` steps exactly one stage: `applied→interviewing`,
  `interviewing→offered`. Any other source state → `409 candidate_stage_conflict`.
- `reject`/`withdraw` accept an optional `note` (1..500, PII) and are allowed
  from `applied|interviewing|offered`; from any terminal state → `409
  candidate_not_open`.
- `hire` is allowed only from `offered` (`409 candidate_stage_conflict`
  otherwise) and only on a job that is `open` (`409 job_not_open`) with free
  openings (`409 job_openings_filled`).
- `schedule` an interview requires the candidate in `interviewing`
  (`409 candidate_stage_conflict`) and an active `interviewer_id`
  (`404 interviewer_not_found` foreign/unknown; `409 interviewer_not_active`
  terminated).
- `complete` (feedback: `rating` 1..5, `notes?` 1..1000) and `cancel` require
  `scheduled` (`409 interview_not_scheduled`); only the assigned interviewer may
  `complete` — a caller with management standing gets `403`, a caller without
  standing `404` — while HR may `cancel`/`update`.
- `update` on a job is allowed while `draft`/`open` only (`409 job_not_open`
  when closed); `open` requires `draft` (`409 job_not_draft`), `close` requires
  `open` (`409 job_not_open`).

Pinned error codes (`409`/`404` responses carry these stable `code` values):

| Code | HTTP | Raised when |
| --- | --- | --- |
| `job_not_found` | 404 | missing, cross-tenant or out-of-scope job id |
| `candidate_not_found` | 404 | missing, cross-tenant or out-of-scope candidate id |
| `interview_not_found` | 404 | missing, cross-tenant or out-of-scope interview id |
| `interviewer_not_found` | 404 | foreign/unknown `interviewer_id` |
| `job_not_draft` | 409 | `open` on a non-`draft` job |
| `job_not_open` | 409 | `close` on a non-`open` job; job `update` on a closed job; `create candidate`/`hire` on a non-`open` job |
| `job_openings_filled` | 409 | `hire` when hired candidates ≥ `openings` |
| `candidate_already_applied` | 409 | duplicate `(tenant, job_id, email)` |
| `candidate_stage_conflict` | 409 | `advance` with no next stage; `hire` not from `offered`; `schedule` on a non-`interviewing` candidate |
| `candidate_not_open` | 409 | `reject`/`withdraw` from `hired`, `rejected` or `withdrawn` |
| `interview_not_scheduled` | 409 | `update`/`complete`/`cancel` from `completed` or `cancelled` |
| `interviewer_not_active` | 409 | assigning a terminated employee as interviewer |
| `employee_email_taken` | 409 | hire whose (possibly overridden) email exists (propagated from the seam) |
| `employee_code_taken` | 409 | hire with a taken `employee_code` (propagated) |
| `employee_manager_invalid` | 400 | hire with an invalid `manager_id` (propagated) |
| `department_not_found` / `position_not_found` / `location_not_found` | 404 | job create/update referencing foreign org ids (propagated) |
| `recruitment_denied` | 403 | role without the capability attempts a management endpoint |

Field bounds (name/email lengths, `openings` < 1, `rating` out of 1..5, note
lengths) are `422`.

```jsonc
Job {
  "id", "tenant_id",
  "title": string,                  // 1..200
  "description": string|null,       // 0..5000
  "department_id", "position_id", "location_id": uuid,
  "openings": int,                  // >= 1
  "status": "draft|open|closed",
  "created_by": uuid,               // actor user id (ADR 0010)
  "opened_at": date-time|null, "closed_at": date-time|null,
  "created_at", "updated_at"
}

JobCreate  { "title", "description"?, "department_id", "position_id",
             "location_id", "openings" = 1 }
JobUpdate  { "title"?, "description"?, "department_id"?, "position_id"?,
             "location_id"?, "openings"? }

Candidate {
  "id", "tenant_id", "job_id",
  "first_name", "last_name", "email", "phone": string|null,   // PII
  "source": "referral|job_board|agency|other",
  "notes": string|null,             // PII
  "status": "applied|interviewing|offered|hired|rejected|withdrawn",
  "stage_note": string|null,        // PII — last reject/withdraw note
  "rejected_from": string|null,     // observed prior state
  "hired_at": date-time|null,
  "employee_id": uuid|null,         // set by the hire seam
  "created_at", "updated_at"
}

CandidateCreate  { "first_name", "last_name", "email", "phone"?, "source",
                   "notes"? }       // names 1..200, email 3..320, notes 1..2000
CandidateUpdate  { "first_name"?, "last_name"?, "email"?, "phone"?,
                   "source"?, "notes"? }
CandidateDecision { "note"?: string }  // 1..500, PII

CandidateDetail: Candidate + "interviews": Interview[]  // management viewers only

HireCandidate {
  "employment_type": "full_time|part_time|contract|intern",
  "hired_at": date,                 // <= server date
  "manager_id"?: uuid, "employee_code"?: string,
  "first_name"?, "last_name"?, "email"?, "phone"?   // override the candidate record
}

Interview {
  "id", "tenant_id", "candidate_id", "job_id",
  "interviewer_id": uuid,           // employee id of the interviewer
  "scheduled_at": date-time,
  "notes": string|null,             // PII
  "status": "scheduled|completed|cancelled",
  "rating": int|null,               // 1..5, set on complete
  "feedback_notes": string|null,    // PII
  "completed_at": date-time|null, "cancelled_at": date-time|null,
  "created_at", "updated_at"
}

InterviewSchedule { "interviewer_id": uuid, "scheduled_at": date-time,
                    "notes"?: string }
InterviewUpdate   { "interviewer_id"?: uuid, "scheduled_at"?: date-time,
                    "notes"?: string }
InterviewFeedback { "rating": int, "notes"?: string }  // rating 1..5, notes 1..1000
```

Dates are `YYYY-MM-DD`, timestamps UTC ISO 8601. All PII fields above are never
logged, never in event payloads, never in audit `changes` values.

### Hire seam port (ADR 0014 pattern)

`apps/api/app/domains/recruitment/repository.py` owns the protocol; the adapter
lives in `app/infrastructure/postgres/` and is wired in
`app/dependencies.py`:

- `create_employee(tenant_id, actor_id, payload, now)` → creates the employee
  (and its first employment period) **through the Employees service**, returning
  `employee_id`. `payload` = the effective `CreateEmployee` fields: names/email/
  phone (candidate record + hire overrides), the job's
  `department_id`/`position_id`/`location_id`, `manager_id`, `employment_type`,
  `hired_at`, `employee_code`. The call participates in the caller's
  transaction so a seam failure rolls back the `HIRED` transition too.

## Events

Contract before producer (AGENTS.md rule 4); produced through the `EventOutbox`
port in the same transaction as the transition (ADR 0012), relayed verbatim to
`workpulse.events`. This set extends the `.ai/domain.md` catalog (its
`candidate.created` / `interview.completed` rows stay; the same change adds
these rows to the catalog table):

| `event_type` | Emitted when | `data` payload |
| --- | --- | --- |
| `job.opened` | `draft → open` | `job_id`, `position_id`, `department_id`, `location_id`, `openings` |
| `job.closed` | `open → closed` | `job_id`, `closed_by` |
| `candidate.created` | candidate added to an open job | `candidate_id`, `job_id`, `source` |
| `candidate.advanced` | `applied → interviewing` or `interviewing → offered` | `candidate_id`, `job_id`, `from`, `to` |
| `candidate.rejected` | reject from a pre-hired state | `candidate_id`, `job_id`, `rejected_from`, `rejected_by` |
| `candidate.withdrawn` | HR records a candidate withdrawal | `candidate_id`, `job_id`, `withdrawn_from` |
| `candidate.hired` | `offered → hired` after the seam succeeds | `candidate_id`, `job_id`, `employee_id`, `hired_by` |
| `interview.scheduled` | interview created | `interview_id`, `candidate_id`, `job_id`, `interviewer_id` |
| `interview.completed` | feedback filed (`scheduled → completed`) | `interview_id`, `candidate_id`, `job_id`, `interviewer_id`, `completed_by` |
| `interview.cancelled` | `scheduled → cancelled` | `interview_id`, `candidate_id`, `job_id`, `cancelled_by` |

Payloads carry identifiers and state only — no names, emails, phones, notes,
ratings or reasons (`.ai/security.md`). `*_by` fields are actor **user** ids
(ADR 0010); `interviewer_id` is an employee id (the assigned person).

## Edge cases

- **Hire atomicity**: the seam fails (e.g. the candidate's email already belongs
  to an employee — `employee_email_taken`) → the whole hire rolls back; the
  candidate stays `offered` and the web surfaces the stable code.
- **Contested openings**: two candidates `offered` for a job with `openings=1` —
  the first hire succeeds, the second `409 job_openings_filled`.
- **Close with candidates in flight**: allowed; existing candidates keep moving
  (advance/reject/withdraw/interviews work), but `create candidate` and `hire`
  are refused (`409 job_not_open`). `closed` is terminal and cannot be re-opened,
  so an in-flight `offered` candidate on a closed job can only be rejected — HR
  closes deliberately.
- **Duplicate application**: same email re-applied to the same job → `409
  candidate_already_applied`; the same email on a *different* job is a separate
  candidate row (allowed).
- **Hire manager validation**: a foreign or non-active `manager_id` propagates
  the Employees seam's `employee_manager_invalid` and rolls the hire back (the
  new employee has no id yet, so self-reference cannot occur).
- **Interviewer termination between schedule and complete**: completing an
  already-scheduled interview is still allowed (the conversation happened);
  only **new** assignments of a terminated employee are refused
  (`409 interviewer_not_active`).
- **Feedback twice**: `complete` on a `completed`/`cancelled` interview → `409
  interview_not_scheduled`; a re-run never emits a second `interview.completed`.
- **Empty pipeline views**: a job with no candidates, a candidate with no
  interviews, and an interviewer with no assignments each render the web empty
  state (never a bare table).
- **Rate/timestamp edges**: `scheduled_at` in the past is allowed (backfilled
  interviews); `hired_at` in the future → `422` (validator, `hired_at ≤ today`).
- **Tenant isolation**: every id is scoped; tenant B sees `404` for tenant A's
  jobs, candidates and interviews.

## Acceptance criteria

- [ ] API (jobs): given HR creates a job, when it is opened and then closed, then the status transitions are `draft → open → closed` with `opened_at`/`closed_at` stamped and `job.opened`/`job.closed` events in the outbox.
- [ ] API (job failure): given a `draft` job, when a non-HR role calls `openJob` → `403 recruitment_denied`; when `openJob` runs twice → `409 job_not_draft`; when a candidate is created on a `closed` job → `409 job_not_open`.
- [ ] API (candidate lifecycle): given an open job, when HR creates a candidate and advances twice, then `hire` runs, then the status path is `applied → interviewing → offered → hired`, the response carries `employee_id`, an Employee row exists via the seam (visible in Employees), and `candidate.created`/`candidate.advanced`/`candidate.hired` events are in the outbox.
- [ ] API (candidate failure): given an `offered` candidate, when `advance` runs → `409 candidate_stage_conflict`; when `hire` runs on an `interviewing` candidate → `409 candidate_stage_conflict`; creating the same email on the same job again → `409 candidate_already_applied`.
- [ ] API (hire failure): given a candidate whose email already belongs to an employee, when `hire` runs, then `409 employee_email_taken` is returned, the candidate remains `offered`, and no employee row is created (atomic rollback).
- [ ] API (openings): given `openings=1` and two `offered` candidates, when the second `hire` runs → `409 job_openings_filled`.
- [ ] API (interviews): given an `interviewing` candidate, when HR schedules an interview and the assigned interviewer completes it with `rating=4`, then the interview is `completed`, `interview.completed` is in the outbox, and the candidate's stage is unchanged (stage moves stay HR-driven).
- [ ] API (interview failure): given a `scheduled` interview, when another interviewer calls `complete` → `404`; when HR (not assigned) calls `complete` → `403`; when `complete` runs on a `completed` interview → `409 interview_not_scheduled`; scheduling on an `applied` candidate → `409 candidate_stage_conflict`.
- [ ] API (denied role): given an Employee- or Finance-role session, when any management endpoint is called (`createJob`, `createCandidate`, `advanceCandidate`, `hireCandidate`, `scheduleInterview`) → `403 recruitment_denied`.
- [ ] API (scoped viewer): given a Manager with an assigned interview, when they `listInterviews?mine=1` they see only their interviews and `getCandidate` for that candidate; a different candidate id → `404`.
- [ ] API (tenant isolation): given tenant A's job/candidate/interview ids, when tenant B calls any recruitment endpoint with them → `404`.
- [ ] API (PII): a sweep of logs, error messages, outbox payloads and audit `changes` finds no candidate name, email, phone, notes or feedback text (identifiers only).
- [x] Contracts: the ten `recruitment/*` v1 schemas validate their payloads, reject unknown or missing fields, and an event-payload contract test proves the identifiers-only shape (`tests/contract/test_recruitment_events.py`).
- [ ] Web: given an HR session, when the `/recruitment` workspace loads, then Jobs/Candidates/Interviews tabs render with loading, empty and error states, pipeline moves (advance/reject/withdraw/hire) and interview actions reflect the pinned `409`/`404` codes as visible messages, and a failed hire (email taken) leaves the candidate `offered` with the error shown.
- [ ] Web (interviewer): given a Manager with an assigned interview, when they open "My interviews", then only their interviews appear and submitting feedback completes the row without a reload.
- [ ] Web (denied): an Employee or Finance user never sees the Recruitment nav, and a direct `/recruitment` URL redirects or renders the denied state (proven in e2e).
- [ ] E2E: `tests/e2e/recruitment.spec.ts` drives HR create job → open → add candidate → advance → schedule interview → the interviewer (second session) files feedback → advance → hire, and the new employee appears in Employees; plus the duplicate-application refusal and the hidden/denied nav for the employee role.

## Decisions (PO, 2026-10-23)

1. **Intake is HR-entered only** — no public application form in v1. Rationale:
   keeps PII intake inside the authenticated app; a careers/apply surface
   (abuse, spam, public PII) is its own feature.
2. **Hire creates the employee** through an Employees seam (ADR 0014 pattern),
   same transaction, seam errors propagate and roll the hire back. Rationale:
   "hiring" closes the jobs→employees loop instead of ending in manual
   re-entry; the seam keeps the contexts decoupled.
3. **Pipeline is lean**: `applied → interviewing → offered → hired | rejected |
   withdrawn`, HR-driven moves, no screening stage. Rationale: enough for real
   hiring; stage-sprawl can grow later additively.
4. **No candidate documents** — contact fields only; presigned upload waits for
   the Phase 4+ security review (product decision 4). Rationale: documents are
   the PII-heaviest surface and their access pattern is undecided.
5. **Events only** — contracts + outbox producers land here; notification
   triggers (ADR 0015 map) and recruitment analytics (time-to-hire, funnel) are
   follow-ups consuming these events. Rationale: same split `overtime.md` used;
   this feature already spans three surfaces.

## Plan

Planned 2026-10-23. Contract-first: the ten event contracts and the outbox
message-key amendment land before any producer. The API contract above is
already pinned; each router task sets those exact `operation_id`s and regenerates
`contracts/openapi/openapi.json` + `@workpulse/api-client`. Order is
events → domain/API → web → e2e, with `make check` green at the end of every
task.

### 1. Acceptance criteria → changes

| AC | Satisfied by |
| --- | --- |
| Jobs lifecycle (`draft → open → closed`, stamps, `job.opened`/`job.closed`) | T2: `jobs` model + `0015` migration, `Job` service transitions, job router routes, event builders from T1 |
| Job failures (`403`, `job_not_draft`, `job_not_open`) | T2: `policies.py` + conditional repository transitions; T3: candidate create checks job `open` |
| Candidate lifecycle + employee created through the seam | T3 candidate transitions; T4 `EmployeeHirePort` adapter + hire route; Employees emits `employee.created`, recruitment emits `candidate.hired` |
| Candidate failures (`candidate_stage_conflict`, `candidate_already_applied`) | T3: conditional `advance`/`hire` source-state updates + unique `(tenant_id, job_id, email)` mapped from `IntegrityError` |
| Hire failure is atomic (`employee_email_taken`, candidate stays `offered`, no employee) | T4: port calls `EmployeeService.create` on the request session (flush-only); the exception rolls back candidate + employee + both outboxes |
| Openings (`job_openings_filled`) | T4: `SELECT … FOR UPDATE` on the job row inside hire, then count `status = 'hired'` |
| Interviews happy path (`schedule` → `complete`, candidate unchanged) | T5: interview service/repository/router + `interview.scheduled`/`interview.completed` producers |
| Interview failures (`404` non-assignee, `403` management non-assignee, `interview_not_scheduled`, `candidate_stage_conflict`) | T5: completion permission ladder + conditional state transitions; schedule checks candidate `interviewing` and interviewer active |
| Denied roles (`403 recruitment_denied`) | T2 `policies.py` `ensure_can_manage` on every management route/service; Employee/Finance never pass |
| Scoped viewer (`?mine=1`, candidate context, other candidate `404`) | T5: non-management interviews are forced to `identity.employee_id`; job/candidate reads scoped through interview assignment |
| Tenant isolation (`404`) | T2–T5 repositories filter tenant first; T2/T3/T5 isolation tests |
| PII sweep | T1 contracts carry identifiers only; T2–T5 audit `changes` record PII fields as `"changed"`; T4/T5 log sweep |
| Ten v1 contracts validate + identifiers-only | T1 schemas, builders, contract tests + catalog/outbox sweeps |
| Web HR workspace (tabs, states, pipeline, interview actions, visible errors) | T6 shell/jobs, T7 candidates/hire, T8 interviews; `ApiError.code` maps in `labels.ts` |
| Web interviewer (“My interviews”, feedback completes without reload) | T8 `MyInterviews` + feedback dialog; mutation invalidates the interviews query key |
| Web denied nav + direct URL | T6 nav entry role gate + `use-recruitment-access` client guard; T9 e2e proves it |
| E2E full flow + duplicate refusal + denied nav | T9 `tests/e2e/recruitment.spec.ts` + `reset_recruitment_fixtures.py` |

### 2. Repo findings the plan relies on (verified 2026-10-23)

- The Employees entry point is **`EmployeeService.create(identity, payload, now)`**
  (`apps/api/app/domains/employees/service.py`), not `EmployeesService`. It
  checks email/code uniqueness, org refs, manager validity, writes the employee
  + first employment period, `employee.created` and `employee.created` audit —
  all with `flush()` only. `get_session` commits once per request, so a port
  constructed with the same `AsyncSession` joins the hire transaction.
- `EmployeeService.create` does not read `identity.role`; the seam port can build
  an `Identity(tenant_id, actor_id, role=Role.HR, employee_id=None)` internally.
- **Outbox blocker:** `app/shared/outbox.py` rejects any envelope whose `data`
  lacks `employee_id` or `run_id`. Only `candidate.hired` carries `employee_id`.
  T1 must extend `_MESSAGE_KEY_FIELDS` (order: `employee_id`, `run_id`,
  `candidate_id`, `job_id`, `interview_id`) and amend ADR 0012; otherwise nine
  recruitment events fail at `append()` with `invalid_event_envelope` (500).
- **Status discrepancy:** the Employees-owned `employee_manager_invalid` is
  HTTP **400**, not the 409 written in this spec's error table. Since the spec
  says seam errors propagate unchanged, T1 corrects the table to 400 and T4
  asserts the real code/status. No remapping in the adapter.
- Existing patterns to mirror: `overtime` (workflow service, conditional
  transitions, audit, outbox, policies, exceptions), `payroll` (domain-owned
  seam Protocol + Postgres adapter wired in `dependencies.py`), `attendance`
  (repository cross-context read against `employees`), `leave`/`overtime`
  (unit fakes, integration workflow/tenant/role tests, web feature layout).
- Test harness: integration `client_factory(identity)` overrides `get_identity`
  and `get_session`; a deterministic `CLOCK` is monkeypatched onto the router's
  `_now`; outbox rows are asserted directly; `conftest.py` truncates an explicit
  table list that must gain the three recruitment tables.
- Web: nav is filtered from `NAV_ITEMS` via `roles`; there is no server-side
  role guard, only `AuthGate` plus a client `router.replace("/dashboard")`
  pattern (`use-payroll-access`). Tabs use local state (Reports). Web tests mock
  `@workpulse/api-client` wholesale; errors map through a `labels.ts` dictionary.
- E2E: `make check-e2e` seeds users/org refs; `full-hop.sh` resets mutable
  fixtures via `apps/api/scripts/reset_*_fixtures.py`; each role uses a separate
  `browser.newContext()`.

### 3. Ordered tasks

#### T1 — Event contracts, catalog, outbox key amendment, ADRs

**Changes**

- Add the ten v1 schemas under `contracts/events/recruitment/`:
  `job-opened`, `job-closed`, `candidate-created`, `candidate-advanced`,
  `candidate-rejected`, `candidate-withdrawn`, `candidate-hired`,
  `interview-scheduled`, `interview-completed`, `interview-cancelled`
  (`additionalProperties: false`, Draft 2020-12, payloads exactly the Events
  table, ids/state/counts only).
- Add pure builders in `apps/api/app/domains/recruitment/events.py`
  (`EventName` Literal, `_envelope`, one keyword-only builder per event) and the
  package `__init__.py`.
- Extend `app/shared/outbox.py` `_MESSAGE_KEY_FIELDS` and its docstring; update
  the `EventEnvelope` gate comment. No producer yet.
- Add **ADR 0020** (`.ai/decisions/0020-recruitment-hiring-seam.md`): hire
  creates the Employee through the Employees service on the caller's session;
  port + adapter, atomic rollback, `employee.created` + `candidate.hired`.
  Amend **ADR 0012** with the 2026-10-23 key-fallback decision.
- Update `.ai/domain.md` (ten catalog rows + Job/Candidate/Interview state
  machines) and `.ai/security.md` (add the *Run recruitment* matrix row:
  Manager `assigned interviews only`, HR/Admin/Super Admin `Y`, Employee/Finance
  `—`).
- Correct the error-table row `employee_manager_invalid` to **400** in this spec
  (see Repo findings).

**Files:** contracts above · `apps/api/app/domains/recruitment/{__init__,events}.py`
· `apps/api/app/shared/outbox.py` · `.ai/decisions/{0012,0020}*` · `.ai/domain.md`
· `.ai/security.md` · `apps/api/tests/contract/test_recruitment_events.py` ·
`apps/api/tests/contract/test_notifications_consumed_events.py` (ten
`BUILDER_CASES` entries) · `apps/api/tests/integration/test_outbox_contracts.py`
(`job`/`candidate`/`interview` → `recruitment` directory mapping) ·
`apps/api/tests/unit/outbox/test_port.py` (fallback key order + rejection).

**Verify:** `make check-api`. Contract tests prove every schema validates its
builder, unknown/PII keys are rejected, keys pass the outbox gate, and each
event's `data` key set matches the pinned table. No e2e change.

#### T2 — Persistence foundation + Jobs API

**Changes**

- `models.py`: `jobs`, `candidates`, `interviews` (all three now, one migration
  to avoid churn). `String` status columns + `CheckConstraint`s, tenant-first
  indexes, FKs to org refs/employees, unique `(tenant_id, job_id, email)` on
  candidates, interview indexes `(tenant_id, candidate_id, status)` and
  `(tenant_id, interviewer_id, scheduled_at)`, created/updated timestamps.
- Migration `apps/api/alembic/versions/0015_recruitment.py` (append-only) with
  the same constraints/indexes; import the model module in `alembic/env.py`;
  add the three tables to the integration `TRUNCATE` tuple.
- `schemas.py`: all wire types now (Job/Candidate/Interview + Create/Update/
  Decision/Hire/Feedback/Detail, list pages, StrEnums, `extra="forbid"` on
  writes, bounds from the pinned JSON).
- `exceptions.py`: every pinned recruitment code as a `WorkPulseError` subclass,
  plus `RecruitmentDeniedError` (403 `recruitment_denied`) and
  `HireDateInFutureError` (422 `validation_error`).
- `policies.py`: `MANAGEMENT_ROLES`, `is_management`, `ensure_can_manage`
  (403 `recruitment_denied`); no role checks in routers beyond calling it.
- `repository.py`: `RecruitmentRepository` Protocol + job filters; `models` never
  leave the adapter. `service.py`: job create/get/list/update/open/close,
  org-ref existence via injected `DepartmentService`/`PositionService`/
  `LocationService` (404s propagate; no archived opinion — see Risks).
- `infrastructure/postgres/repositories/recruitment.py`: job methods with
  tenant-first `WHERE`s, conditional transitions, `ILIKE` `q` escaped.
- `api/v1/recruitment.py`: the six job routes with the pinned `operation_id`s;
  mount in `api/v1/__init__.py`; `get_recruitment_service` + `RecruitmentServiceDep`
  in `dependencies.py`.
- Unit tests (`tests/unit/recruitment/`) and integration
  `test_recruitment_jobs.py`.

**Verify:** `make gen-api` (OpenAPI + client), then `make check-api` and
`make check-web`. Covers AC jobs + job failure (`403`, `job_not_draft`,
`job_not_open`), audit and outbox rows, tenant isolation.

#### T3 — Candidates API (no hire)

**Changes**

- Repository candidate methods (create with `IntegrityError` → 
  `candidate_already_applied`; get; list with `job_id`/`status`/`q`; update;
  conditional advance/reject/withdraw; `get`/`list` scoped by visible candidate
  ids through interviews).
- Service: create (job must be `open`), list/get/update, advance
  (`applied→interviewing`, `interviewing→offered` only), reject/withdraw from
  `applied|interviewing|offered`, audit with PII fields as `"changed"`, events
  `candidate.created`/`advanced`/`rejected`/`withdrawn`.
- Router: create/list/get/update/advance/reject/withdraw with pinned
  `operation_id`s. `getCandidate` returns `CandidateDetail`; `interviews` is
  populated in T5 (empty until then).
- Scope: management sees all; Manager/other interviewers see only candidates
  they have an interview for (`404` otherwise); Employee/Finance on
  management mutations → `403`.
- Unit fake repo with `lose_next_transition`; integration
  `test_recruitment_candidates.py` + tenant isolation.

**Verify:** `make gen-api`, `make check-api`. Covers candidate lifecycle except
hire, duplicate application, wrong-state `409`s, PII audit.

#### T4 — Hire seam + hire endpoint

**Changes**

- `EmployeeHirePort` Protocol + `EmployeeHirePayload` dataclass in
  `domains/recruitment/repository.py` (recruitment-owned, no Employees import).
- `infrastructure/postgres/recruitment_hire.py`: `PostgresEmployeeHirePort`
  builds the effective `CreateEmployee` (candidate names/email/phone + overrides,
  job org ids, manager, type, date, code), calls `EmployeeService.create` on the
  same session, returns `employee_id`. Refactor `get_employee_service` into a
  `build_employee_service(session)` helper so the adapter and HTTP path share
  wiring.
- Service `hire`: lock candidate (`FOR UPDATE`), require `offered`; load/lock the
  job, require `open`, count hired `< openings`; validate `hired_at <= now.date()`
  (422); call the port; conditional `offered→hired` update setting
  `employee_id`/`hired_at`; append `candidate.hired`; audit. Any seam error
  propagates and rolls the session back (candidate stays `offered`).
- Router `POST …/{candidate_id}/hire` (`hireCandidate`), response
  `CandidateDetail`. Wire the port + service in `dependencies.py`.
- Unit: fake port raises every seam error; assert candidate unchanged, no
  outbox/audit. Integration: employee + employment + both events; email-taken
  rollback; openings=1 contested hires; manager-invalid (400); tenant isolation.

**Verify:** `make gen-api`, `make check-api`.

#### T5 — Interviews API + scoped viewer

**Changes**

- Repository: create/get/list/update/cancel/complete (conditional on
  `scheduled`), `interviewer_facts` (active check against `employees` in the
  adapter, the attendance cross-context pattern), visible job/candidate id
  queries.
- Service: schedule only on `interviewing` candidates and active employees
  (`interviewer_not_found` 404 / `interviewer_not_active` 409); update/cancel
  management-only while `scheduled`; complete only when
  `identity.employee_id == interviewer_id`, else management → 403
  `recruitment_denied`, non-management → 404; candidate stage never changes.
- List: `candidate_id`/`job_id`/`status`/`mine`/`interviewer_id`/`from`/`to`
  (dates, inclusive UTC days); non-management forced to own `employee_id` and
  rejected (`403 recruitment_denied`) if they pass `interviewer_id`; foreign
  filter ids 404.
- Router: schedule/list/update/complete/cancel with pinned `operation_id`s;
  populate `CandidateDetail.interviews` (management: all; interviewer: own).
- Unit + integration `test_recruitment_interviews.py`, role matrix, tenant
  isolation.

**Verify:** `make gen-api`, `make check-api`.

#### T6 — Web shell, navigation, Jobs

**Changes**

- `features/recruitment/roles.ts` (`RECRUITMENT_MANAGER_ROLES` = HR/Admin/Super
  Admin; `RECRUITMENT_NAV_ROLES` = Manager + those), `types.ts`, `api.ts`
  (parameterless query-key invalidation for jobs/candidates/interviews),
  `labels.ts` (status badges + every pinned `ApiError.code` → sentence),
  `schema.ts`, `dates.ts`, `hooks/use-recruitment.ts`.
- `app/(dashboard)/recruitment/page.tsx` → `RecruitmentWorkspace` (Tabs: Jobs,
  Candidates, Interviews for management; My interviews for interviewers) with
  `use-recruitment-access` (Employee/Finance redirect to `/dashboard`; Manager
  only My interviews).
- `components/Job…`: list with status/q/department filters (debounced),
  pagination, create/edit dialog, open/close, loading/empty/error states.
- Add `Recruitment` to `NAV_ITEMS` with `RECRUITMENT_NAV_ROLES` and an icon; add
  `/recruitment` to `proxy.ts` `DASHBOARD_ROOTS` + matcher.
- Vitest: `tests/recruitment/jobs.test.tsx`, `errors.test.ts`, `schema.test.ts`.

**Verify:** `make check-web`, then `make check`.

#### T7 — Web candidates, pipeline and hire

**Changes**

- Candidates tab: job/status/q filters, paginated table, create/edit dialog,
  row actions (advance/reject/withdraw) with note dialogs, hire dialog
  (employment type, hired date ≤ today, manager picker, employee code, name/
  email/phone overrides).
- Mutations invalidate the candidate + job + interview base keys; errors surface
  through `labels.ts` (toast + inline root alert). A failed hire leaves the row
  `offered` and shows the mapped message.
- Empty pipeline state for a job with no candidates; loading/error/denied.
- Vitest: `tests/recruitment/candidates.test.tsx` (pipeline + 409 + hire
  rollback display), schema tests.

**Verify:** `make check-web`, then `make check`.

#### T8 — Web interviews and “My interviews”

**Changes**

- Interviews tab (management): schedule dialog on an `interviewing` candidate,
  reschedule/reassign/cancel while `scheduled`, rating/notes feedback view.
- My interviews (Manager/HR/Admin/Super Admin): only rows where
  `interviewer_id === user.employee_id` (`mine=1`), candidate/job labels resolved
  from the scoped lists, empty state when unassigned. Feedback dialog submits
  `rating` 1..5 + optional notes; success invalidates the interviews key so the
  row becomes `completed` without a reload. Non-management `update`/`cancel` are
  not rendered (and the API is the authority).
- Vitest: `tests/recruitment/interviews.test.tsx` (feedback completes, empty,
  errors), `my-interviews.test.tsx`.

**Verify:** `make check-web`, then `make check`.

#### T9 — E2E, fixtures, docs

**Changes**

- `apps/api/scripts/reset_recruitment_fixtures.py`: tenant-scoped delete of
  interviews → candidates → jobs, plus employees/employments created by those
  hires; call it in `tests/e2e/full-hop.sh` and at the start of the spec (the
  overtime pattern).
- `tests/e2e/recruitment.spec.ts`: HR session creates/opens a job, adds a
  candidate, advances once, schedules an interview for the seeded manager;
  the Manager opens “My interviews” in a second browser context, files
  `rating=4`; HR advances to `offered` and hires; the new employee appears in
  Employees. Also: duplicate application shows the pinned `409` message; the
  Employee session has no Recruitment nav and a direct `/recruitment` redirects.
- No new seed required (job/candidate data is created through the UI with a
  per-run unique email).
- Update this spec's acceptance checkboxes, `.ai/features/README.md` status, and
  `tasks/TODO.md`.

**Verify:** `make check` (all gates, including e2e).

### 4. Test strategy

| Layer | Files | What it proves | Gate |
| --- | --- | --- | --- |
| Unit – policies | `tests/unit/recruitment/test_policies.py` | HR/Admin/Super Admin allowed; Employee/Finance/Manager denied on management mutations | `check-api` |
| Unit – services | `tests/unit/recruitment/test_jobs_service.py`, `test_candidates_service.py`, `test_hire_service.py`, `test_interviews_service.py` | State machines, conditional-update race (`lose_next_transition`), scoped visibility, audit + outbox payloads, PII exclusion, hire atomicity with a failing fake port | `check-api` |
| Integration – jobs | `tests/integration/test_recruitment_jobs.py` | `draft→open→closed`, stamps, events, `403`, `job_not_draft`, `job_not_open`, tenant `404` | `check-api` |
| Integration – candidates/hire | `test_recruitment_candidates.py`, `test_recruitment_hire.py` | Lifecycle, duplicate `409`, wrong-state `409`, employee row + employment, email-taken rollback, openings, manager-invalid 400 | `check-api` |
| Integration – interviews | `test_recruitment_interviews.py` | Schedule/complete/cancel, non-assignee `404`, management non-assignee `403`, wrong state `409`, stage unchanged, scope filters | `check-api` |
| Integration – roles/tenancy/PII | `test_recruitment_roles.py`, `test_recruitment_tenant_isolation.py`, `test_recruitment_logs.py` | Matrix rows, every endpoint tenant `404`, no candidate/feedback text in logs/errors/audit/outbox | `check-api` |
| Contract | `tests/contract/test_recruitment_events.py`, existing catalog/outbox sweeps | Ten schemas validate builders, identifiers-only, outbox gate, OpenAPI drift | `check-api` |
| Web | `apps/web/tests/recruitment/*.test.tsx|ts` | Loading/empty/error, code→message, pipeline actions, hire rollback display, feedback completes | `check-web` |
| E2E | `tests/e2e/recruitment.spec.ts` | HR + interviewer two-session flow, employee visible in Employees, duplicate refusal, hidden/denied nav | `check-e2e` (inside `make check`) |

Failure cases are explicit: job `403`/`409`; candidate `409
candidate_stage_conflict`/`candidate_already_applied`/`candidate_not_open`;
hire `409 employee_email_taken` + rollback and `409 job_openings_filled`;
interview `404`/`403`/`409 candidate_stage_conflict`/`409
interview_not_scheduled`; web renders each pinned code without the raw code;
e2e proves duplicate refusal and denied navigation.

### 5. Risks, unknowns, decisions

1. **Outbox key gate (blocker).** Nine of ten recruitment events carry no
   `employee_id`/`run_id`; T1 must land the key fallback before any producer or
   every transition 500s. ADR 0012 amendment records the order
   `employee_id → run_id → candidate_id → job_id → interview_id`.
2. **`employee_manager_invalid` status.** The spec table originally said 409;
   Employees raises 400. T1 corrected the table to 400 (the seam propagates it
   unchanged), and T4 asserts the real code/status.
3. **`CandidateDetail.interviews` scope.** The table comment says “management
   viewers only”; the scoped-viewer rule needs an assigned interviewer to see
   their own feedback. Plan: management receives all interviews, an assigned
   interviewer receives only their own, everyone else is `404`; T1 updates the
   comment to say this.
4. **Archived org refs.** The spec pins only foreign-id `404`s for job org refs.
   The plan accepts any *existing* ref at job create/update (no unpinned
   `409`). If the ref is archived by hire time, Employees’ existing
   `transfer_target_archived` (409) propagates and the hire rolls back. If
   product wants archived refs refused at job creation, that is a one-line
   addition with a new pinned code.
5. **Email uniqueness is exact.** The unique index is `(tenant_id, job_id,
   email)`, so casing variants are distinct. If case-insensitive application
   matching is required, normalize the email in the schema and use a
   `lower(email)` unique index in migration `0015` before the first release.
6. **`hired_at` future check** is service-level (Pydantic cannot read the
   injected clock); it raises 422 `validation_error`, preserving the
   time-as-a-parameter rule.
7. **Concurrency:** candidate hire locks the candidate then the job; job
   open/close locks the job only, and candidate creation reads the job without a
   lock, so the lock order has no cycle. Duplicate application relies on the
   unique constraint + `IntegrityError` mapping, not a pre-check race.
8. **Web tabs use local state** (the Reports pattern); filters are not
   deep-linkable. That is consistent with the app and avoids introducing
   `useSearchParams` in this feature.
9. **Scope queries:** non-management job/candidate visibility is derived from
   their interview assignments; the interview indexes cover it. No WebSocket or
   realtime work is in scope (Events only, PO Decision 5).
10. **ADRs:** ADR 0020 (hire seam) + ADR 0012 amendment (message keys). No other
    expensive-to-reverse decision is introduced.

### 6. Definition of done

- Every acceptance box above is ticked with a test reference.
- `make check` green (API + web + e2e), `make gen-api` diff-free.
- Ten v1 contracts in `contracts/events/recruitment/`; catalog, security matrix
  and state machines updated in `.ai/domain.md` / `.ai/security.md`.
- Audit entries on every mutation, no PII in logs/errors/audit values/outbox
  payloads, tenant isolation proven for each domain.
- Feature spec status `done`; `.ai/features/README.md` and `tasks/TODO.md`
  moved in the same change.
