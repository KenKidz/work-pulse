# Feature: Identity & Access

- **Status:** draft
- **Phase:** 2
- **Size:** M
- **Surfaces:** api+web
- **Links:** ADR 0004 (dev identity → real authN swap) · ADR 0006 (argon2-cffi is
  a pre-agreed Phase 2 dependency) · `.ai/security.md` (role matrix, threat
  model) · `.ai/domain.md` (Identity & Access context) · `.ai/features/employees.md` +
  `attendance.md` (consumers of the identity, updated by this feature)

## Problem

Phase 1 trusts three request headers as identity (`X-Tenant-Id`, `X-Actor-Id`,
`X-Role`, ADR 0004). That is honest scaffolding for local work and unsafe
everywhere else: there are no real accounts, anyone who can set a header is any
role in any tenant, and nothing can be deployed. Phase 2 must replace the dev
identity with real authentication and user accounts — while keeping the policy
layer intact, exactly as ADR 0004 promised: swap the identity source and
nothing else.

## Business rules

- **Credentials**: email + password. Passwords are hashed with argon2-cffi;
  plaintext is never stored, logged, echoed in errors, or put in audit entries.
- **User emails are unique globally** (decision 7): one account belongs to
  exactly one tenant, and login resolves the tenant from the account.
- A user belongs to exactly one tenant; `tenant_id` is on every row and every
  query is scoped in repositories (`.ai/security.md`).
- **One of six fixed roles per user**: `employee | manager | hr | finance |
  admin | super_admin` (decision 3). The `.ai/security.md` matrix is the
  capability source; roles are not user-editable in this feature.
- A user may optionally link to **at most one employee of the same tenant**
  (`employee_id`, nullable — finance/super_admin accounts need no employee).
  An employee has at most one user.
- **Sessions** (decision 1): opaque random 256-bit token, stored **hashed**
  (SHA-256 — the token is high-entropy, the hash is a lookup key) in a
  `sessions` table with an absolute `expires_at` (proposed: 7 days). Delivered
  as an httpOnly `wp_session` cookie (`SameSite=Lax`; `Secure` outside local
  dev). Logout deletes the row; every request re-resolves user + role from the
  session, so role changes and deactivation take effect immediately.
- **No user enumeration**: a wrong password and an unknown email both fail with
  an identical `401 invalid_credentials`.
- Changing own password requires the current password; an admin reset does not.
  **Both revoke every existing session of that user** (decision 4).
- A deactivated user cannot log in, and any existing session of theirs is
  rejected (`401 unauthenticated`).
- (proposed) A tenant must retain at least one active `admin`/`super_admin`
  user; deactivating the last one is rejected.
- (proposed) Password rule: minimum 10 characters, no complexity requirements.
- Authorization stays in `domains/<name>/policies.py`; the web app only hides
  UI. Cross-tenant reads return **404**, never 403.
- The identity consumed by policies keeps its shape — `{tenant_id, actor_id,
  role}` — but `actor_id` becomes the **user id**, and the identity gains
  `employee_id: UUID | None`. Consumers that treated the actor as an employee
  switch to `identity.employee_id` (see Surfaces & files).
- Sensitive mutations write audit entries (append-only, no password material):
  user create, update, role change, activate, deactivate, password change,
  password reset.
- Login/logout are not audited (not mutations); failed logins are not stored
  (no PII accumulation).

## Scope

- `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` ·
  `PUT /auth/password` (change own password).
- User management for Admin/Super Admin: list, create, get, update
  (email / role / employee link), activate, deactivate, reset password.
- The identity dependency swap: `app/shared/identity.py` stops reading dev
  headers and resolves the identity from the session cookie.
- Consumer adaptation in Employees and Attendance for the `actor_id` →
  `employee_id` change (`/employees/me`, manager scoping, check-in/out self).
- Web: login page, logout action, Settings → Users management page; the
  api-client mutator drops the dev headers (cookies flow automatically).
- Test migration: identity dependency overrides in API tests; real UI login in
  e2e; seeded synthetic users in `database/postgres/seeds/`.

## Out of scope

- **Tenant provisioning / cross-tenant super admin** (decision 6):
  `super_admin` remains a role within a tenant.
- Self-registration, invite emails, email-based password reset — all need
  email delivery (Phase 3 Notifications).
- "Log out everywhere" UI and per-device session management (revocation on
  credential change is automatic and sufficient here).
- Custom roles / permission grants (the `Role`, `PermissionGrant` aggregates in
  `.ai/domain.md` stay future).
- OAuth/SSO, MFA, passkeys.
- Login rate limiting / lockout (Phase 7, roadmap).
- Audit-log read API (the matrix's "Read audit log" row gets its own spec).
- `user.*` events: the event catalog gains none (decision 5) — repudiation is
  the audit log's job.

## Surfaces & files

| Surface | Location |
| --- | --- |
| API domain | `apps/api/app/domains/identity/` (users, sessions, credentials) |
| API router | `apps/api/app/api/v1/auth.py` · `apps/api/app/api/v1/users.py` |
| Identity dependency | `apps/api/app/shared/identity.py` (dev headers → session-resolved `Identity`) |
| Persistence | `apps/api/app/infrastructure/postgres/` (users + sessions migration) |
| Seeds | `database/postgres/seeds/` (seeded synthetic users for dev/e2e) |
| Web feature | `apps/web/features/auth/` · `apps/web/features/users/` |
| Web routes | `apps/web/app/(auth)/login/` · `apps/web/app/(dashboard)/settings/users/` |
| API tests | `apps/api/tests/unit` · `tests/integration` · `tests/contract` |
| E2E | `tests/e2e/` (login flow; dev-header injection removed) |

Consumer adaptations (same change; corresponding specs updated with the why):

- `employees/service.py` `me()` resolves via `identity.employee_id` (→
  `404 employee_not_found` when the user has no employee link); manager
  scoping compares `employee.manager_id` against `identity.employee_id`.
- `attendance/service.py` check-in/out resolve the acting employee via
  `identity.employee_id` (today they use `identity.actor_id`).

## Roles & access

| Capability | Employee | Manager | HR | Finance | Admin | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| Login / logout / `GET /auth/me` | Y | Y | Y | Y | Y | Y |
| Change own password | Y | Y | Y | Y | Y | Y |
| List / read users | — | — | — | — | Y | Y |
| Create / update / activate / deactivate users | — | — | — | — | Y | Y |
| Reset a user's password | — | — | — | — | Y | Y |

(Matches the `.ai/security.md` row "Manage users, roles, permissions".)

## API contract (pinned 2026-09-29)

Auth — session cookie `wp_session`; no bearer tokens in this phase:

| Method & path | Input | Response |
| --- | --- | --- |
| `POST /api/v1/auth/login` | `{"email", "password"}` | `200 User` + `Set-Cookie` · `401 invalid_credentials` |
| `POST /api/v1/auth/logout` | — | `204` (idempotent; safe without a session) |
| `GET /api/v1/auth/me` | — | `200 User` · `401 unauthenticated` |
| `PUT /api/v1/auth/password` | `{"current_password", "new_password"}` | `204` · `401 unauthenticated` · `400 invalid_password` (wrong current) |

User management (Admin/Super Admin only; `403 forbidden` for every other role):

| Method & path | Input | Response |
| --- | --- | --- |
| `GET /api/v1/users` | `?q=&role=&active=&page=&page_size=` | `200 {"items": User[], "page": Page}` |
| `POST /api/v1/users` | `{"email", "password", "role", "employee_id"?}` | `201 User` |
| `GET /api/v1/users/{id}` | — | `200 User` · `404` |
| `PATCH /api/v1/users/{id}` | `{"email"?, "role"?, "employee_id"?}` | `200 User` |
| `POST /api/v1/users/{id}/activate` | — | `200 User` |
| `POST /api/v1/users/{id}/deactivate` | — | `200 User` |
| `POST /api/v1/users/{id}/password` | `{"new_password"}` | `204` |

Shapes:

```json
User { "id", "tenant_id", "email", "role": "employee|manager|hr|finance|admin|super_admin",
       "employee_id": null, "active": true, "created_at", "updated_at" }
```

`User` never carries password material or session data. Ids/timestamps follow
the Phase 1 conventions (UUIDv7, `timestamptz` UTC). operationIds (orval hook
names): `login`, `logout`, `getCurrentUser`, `changePassword`, `listUsers`,
`createUser`, `getUser`, `updateUser`, `activateUser`, `deactivateUser`,
`resetUserPassword`.

### Pinned details

- **Identity object** (the swap ADR 0004 froze the shape of):
  `{tenant_id, actor_id /* user id */, role, employee_id /* UUID | None */}`.
  Resolved per request from the session → user row; the dev-identity headers
  are deleted, not kept behind a flag (decision 5).
- **Pagination/filters** reuse the pinned Phase 1 shape (`?page=`, `?page_size=`
  clamped, `Page` object). `q` = case-insensitive substring of `email`; `role`
  exact; `active` bool. Default order `email` ascending.
- **PATCH semantics**: absent field = unchanged; explicit `"employee_id": null`
  unlinks. Email change re-checks global uniqueness. `active` is never in
  PATCH — use the explicit activate/deactivate routes (archive/restore idiom).
- **Employee link**: `employee_id` must reference an employee of the same
  tenant (else `404 employee_not_found`, consistent with cross-tenant 404) and
  must not already be linked (`409 employee_link_taken`).
- **Error code → status**: `invalid_credentials` 401 (login only) ·
  `unauthenticated` 401 (missing/unknown/expired session, or the account was
  deactivated) · `invalid_password` 400 (wrong current password) ·
  `forbidden` 403 · `user_not_found` 404 · `user_email_taken` /
  `employee_link_taken` / `last_admin` 409 · `validation_error` 422. Body
  always `{"error": {"code", "message"}}`; messages are static strings.
- **Password material** never appears in responses, error messages, logs, or
  audit entries (audit records "password changed", never values).

## Events

None — decision 5. Login/session lifecycle is not a workforce fact; the event
catalog in `.ai/domain.md` is unchanged. Sensitive mutations are covered by
audit entries (rules above). (`employee.terminated` → Identity remains a Phase 3
consumer concern, out of scope here.)

## Edge cases

- Unknown email and wrong password → byte-identical `401 invalid_credentials`.
- Missing, malformed, expired or revoked session token → `401 unauthenticated`.
- Old session of a deactivated user → `401 unauthenticated`; login as that user
  → `401 invalid_credentials`.
- Create user with an email that exists **in another tenant** → `409
  user_email_taken` (global uniqueness — existence of the account is leaked to
  admins here by design of decision 7; never to unauthenticated callers).
- `employee_id` pointing at another tenant's employee → `404 employee_not_found`.
- Employee already linked to a user → `409 employee_link_taken`.
- (proposed) Deactivating the last active admin/super_admin → `409 last_admin`.
- Password change/reset revokes all of that user's sessions: their other tabs
  get `401` on the next request.
- Role change is effective on the next request (identity re-resolved per
  request) — no stale-role window.
- Logout without a session → `204` (idempotent).
- `PATCH` linking/unlinking an employee that is terminated — allowed (accounts
  and employment are separate lifecycles; deactivation is its own action).

## Acceptance criteria

- [ ] API (authN): given a seeded user, when `POST /auth/login` with the correct
      email + password, then `200 User`, a `wp_session` cookie is set, and
      `GET /auth/me` returns that user.
- [ ] API (failure): wrong password and unknown email both return `401
      invalid_credentials` with identical bodies.
- [ ] API (failure): a request with a missing/unknown/expired session returns
      `401 unauthenticated` on every protected route.
- [ ] API (revocation): after `PUT /auth/password` succeeds, the pre-change
      session is rejected with `401`, and logging in with the new password works.
- [ ] API (denied role): an HR user calling `POST /api/v1/users` gets `403
      forbidden`; only admin/super_admin may manage users.
- [ ] API (tenant isolation): tenant B's admin calling `GET /users/{id}` for a
      tenant A user gets `404 user_not_found`, and `GET /users` never lists
      cross-tenant rows.
- [ ] API (users): admin creates a user with an employee link → `201`; duplicate
      global email → `409 user_email_taken`; already-linked employee → `409
      employee_link_taken`; other tenant's employee → `404 employee_not_found`.
- [ ] API (failure): a deactivated user cannot log in (`401`) and an existing
      session is rejected; `activate` restores access.
- [ ] API (audit): user create, update, activate, deactivate, password change
      and password reset each write one audit entry carrying the acting user id
      and no password values.
- [ ] API (consumer adaptation): for a session whose user links to employee E,
      `GET /employees/me` returns E and check-in opens E's record — regression
      for the `actor_id` → `employee_id` swap.
- [ ] Web: the login form authenticates and lands on the dashboard; wrong
      credentials show the error state; unauthenticated visits to `(dashboard)`
      routes redirect to the login page.
- [ ] Web: Settings → Users (admin) lists users and the create / edit /
      deactivate / reset-password flows render success and `ApiError` error
      states; the page is unreachable and hidden for non-admin roles.
- [ ] Web (failure): submitting the login form with bad credentials keeps the
      user on the page and surfaces the `invalid_credentials` message.
- [ ] E2E: a seeded user logs in through the UI and completes a protected flow
      (e.g. attendance check-in); a request carrying only the old dev-identity
      headers (no session) is rejected.

## Decisions (PO, 2026-09-29)

1. **AuthN = email + password (argon2-cffi) with DB-backed sessions.** Opaque
   token, hashed in a `sessions` table, httpOnly cookie; logout deletes the
   row. Chosen over JWT (ADR 0006's pre-agreed pyjwt stays unused): trivial
   revocation, no new infra before Redis earns its place ("boring first").
2. **Users are admin-provisioned with an optional employee link.** No
   self-registration; `employee_id` nullable so non-employee accounts
   (finance, super_admin) work; `/employees/me` resolves through the link.
3. **Roles stay the fixed six, one per user.** Phase 2 swaps the identity
   source (ADR 0004), not the role model; policies and the matrix are
   unchanged. Custom roles/permission grants stay future.
4. **Password lifecycle = change own (current password required) + admin
   reset.** No email-based reset — email delivery is Phase 3 Notifications.
   Credential changes revoke all of the user's sessions.
5. **Dev headers are deleted, not flagged.** Unit/integration tests construct
   the identity directly or use FastAPI dependency overrides; e2e logs in via
   the real UI. Honors ADR 0004's "replace before any deployment" with no
   escape hatch.
6. **Tenant provisioning out of scope.** `super_admin` remains a per-tenant
   role; tenant creation/administration gets its own spec.
7. **Login resolves the tenant from the account** — user emails are unique
   globally (`POST /auth/login {email, password}`). One dev tenant exists and
   provisioning is out of scope; if one person ever needs accounts in two
   tenants, login gains a disambiguation step later.

Technical defaults recorded here, cheap to reverse: session absolute TTL
(proposed 7 days), password minimum 10 characters, cookie name `wp_session`,
SHA-256 token lookup hash, (proposed) `409 last_admin` guard.

## Plan

_Not planned yet._
