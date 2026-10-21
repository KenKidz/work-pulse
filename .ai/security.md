# WorkPulse — Security & access control

> HR data is sensitive: identity, salary, leave reasons (health-adjacent),
> documents. This file is the contract every feature spec must respect.

## Threat model, briefly

1. **Cross-tenant leakage** — the highest-severity risk in a multi-tenant HR
   platform. Must fail closed.
2. **PII exposure** — through logs, error messages, exports, analytics tables,
   event payloads, and public URLs.
3. **Privilege escalation** — an employee approving their own leave, a manager
   reaching another team, a role gaining a capability it was not granted.
4. **Repudiation** — "who changed this salary?" must always have an answer.

## Roles & permission matrix

Capability rows are the product's real authorization surface. `Y` = allowed,
`team` = only their own team, `own` = only their own data.

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View own profile / attendance / payslips | own | own | own | own | own | own |
| Check in / out | own | own | own | own | own | own |
| Request leave / overtime | own | own | own | own | own | own |
| View own notification inbox & mark read | own | own | own | own | own | own |
| View team attendance & profiles | — | team | Y | — | Y | Y |
| Approve leave / overtime | — | team | Y | — | Y | Y |
| Manage employee records (create/update/terminate) | — | — | Y | — | Y | Y |
| Manage organization structure | — | — | Y | — | Y | Y |
| View company-wide reports | — | — | Y | Y | Y | Y |
| Configure & run payroll | — | — | view | Y | Y | Y |
| Manage users, roles, permissions | — | — | — | — | Y | Y |
| Provision / administer tenants | — | — | — | — | — | Y |
| Read audit log | — | team | Y | Y | Y | Y |

## Authorization model

- **RBAC first** (Phase 2): a user holds roles; roles hold capabilities.
  Enforced in `apps/api/app/domains/<name>/policies.py` — never in routers,
  never in the web app (the UI hides, the API enforces).
- **ABAC later** (Phase 4+): attributes such as "manager of this employee",
  "same location", "within approval threshold".
- **Identity is session-resolved** (since Phase 2, ADR 0010): every request
  resolves `Identity {tenant_id, actor_id /* user id */, role, employee_id}`
  from the `wp_session` cookie in one dependency (`app/dependencies.py`) — the
  Phase 1 dev headers (ADR 0004) are deleted, not flagged. Policy code
  consumes that identity unchanged; the swap changed the source and nothing
  else. Audit `actor_id` and event-envelope `actor_id` carry the authenticated
  user id from the swap on (pre-swap rows carry employee ids).
- Fail closed: an unknown role, a missing tenant, or a policy that cannot decide
  → deny.
- Cross-tenant reads return **404** (not 403) so existence is not leaked.

## Multi-tenancy

Decided (ADR 0004, 2026-09-24): multi-tenant from the first migration, not a
retrofit.

- `tenant_id` on every table, every row, every event, every audit entry.
- Scoping is enforced in **repositories** (`WHERE tenant_id = :tenant`), so a
  missing filter is a code-review failure, not a runtime hope.
- Every domain ships a tenant-isolation test: data written as tenant A is
  invisible and unmodifiable as tenant B (assert 404).

## PII handling

| Rule | Detail |
| --- | --- |
| Never log PII | names, emails, phones, salaries, leave reasons, documents, tokens. Log ids + outcome only |
| Minimal event payloads | events carry identifiers and state, not personal data |
| Error messages | stable codes, no field values echoed back |
| Analytics store | pseudonymized where possible; salary-grade instead of salary when the question allows |
| Documents | presigned, short-lived URLs; never public paths (Phase 4+) |
| Field-level encryption | salary and document metadata (Phase 4+, ADR required) |
| Fixtures & seeds | synthetic data only — never real people |

## Audit log

- Append-only and immutable: insert-only table, no update/delete path in code.
- Written for every sensitive mutation: employee create/update/transfer/
  terminate, attendance correction, leave & overtime decisions, payroll runs,
  permission changes.
- Entry shape: `id`, `tenant_id`, `actor_id`, `action`, `entity_type`,
  `entity_id`, `occurred_at`, `reason?`, `changes?` (field names + old/new for
  non-PII fields; PII changes recorded as "field changed", not values).
- Introduced in **Phase 1** with the first mutating feature (Employees), because
  retrofitting an audit trail is expensive and the product promises it.
- **Not every mutation is sensitive.** Notification read-state mutations (mark
  read / mark all read — `notification-inbox.md` rule 7) change no PII and no
  business fact: they write **no audit entry** and emit **no event**. UI state
  is not a sensitive mutation; a reviewer should not have to infer this.

## Secrets & configuration

- Environment variables only; `.env` is gitignored, `.env.example` stays current.
- No secrets in code, contracts, events, fixtures, tests, or error messages.
- Dev-only credentials in `docker-compose.yml` are explicitly dev-only and must
  never be reused in a deployed environment.

## Checklist for every feature spec

- [ ] Which roles may call each endpoint? (written in the spec's API contract)
- [ ] Does it mutate sensitive data? → audit entry + event. (Answered
      *no* for notification read state: no audit entry, no event — see
      **Audit log** above and `notification-inbox.md` rule 7.)
- [ ] Is every query tenant-scoped in the repository?
- [ ] Is there a tenant-isolation test and a denied-role test?
- [ ] Does any log line, error message, event payload, or analytics row contain PII?