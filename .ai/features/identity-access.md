# Feature: Identity & Access

- **Status:** implementing
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
  `sessions` table with an absolute `expires_at` (7 days). Delivered as an
  httpOnly `wp_session` cookie (`SameSite=Lax`; `Secure` outside local dev).
  Logout deletes the row; every request re-resolves user + role from the
  session, so role changes and deactivation take effect immediately. The exact
  token/cookie mechanics are pinned in **API contract → Pinned details**.
- **No user enumeration**: a wrong password and an unknown email both fail with
  an identical `401 invalid_credentials`.
- Changing own password requires the current password; an admin reset does not.
  **Both revoke every existing session of that user** (decision 4).
- A deactivated user cannot log in, and any existing session of theirs is
  rejected (`401 unauthenticated`).
- A tenant must retain at least one active `admin`/`super_admin` user;
  deactivating the last one is rejected (`409 last_admin`, service-level).
- Password rule: minimum 10 characters, no complexity requirements.
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
  are deleted, not kept behind a flag (decision 5). `DevIdentity` is renamed
  `Identity`. A user with no employee link hitting an employee-scoped flow
  (`/employees/me`, check-in/out, employee self-scope) gets `404
  employee_not_found` (or the route's existing not-found code) — never a
  server error.
- **Sessions & cookie** (T1): opaque token = 32 random bytes
  (`secrets.token_urlsafe` — the 256-bit secret); the cookie carries the raw
  token, `sessions.token_hash` stores its SHA-256 hexdigest (lookup key
  only). Absolute TTL **7 days** (`expires_at`); expired rows are rejected on
  read. Cookie `wp_session`: `HttpOnly`, `SameSite=Lax`, `Path=/`; `Secure`
  toggled by a settings flag (`SESSION_COOKIE_SECURE`, on outside local dev).
  Logout deletes the row and clears the cookie; logout without a session is
  `204`.
- **Passwords** (T1): argon2id via argon2-cffi `PasswordHasher` defaults
  (t=3, m=64 MiB, p=4 — ADR 0006's pre-agreed dependency; `pyjwt` stays
  unused). Hashing/verification is sync CPU work and runs off the event loop
  (`anyio.to_thread`). Minimum 10 characters, no complexity rules (shorter →
  `validation_error` 422). Email is a plain string (consistent with
  employees) — no new dependency.
- **Audit actions** (T1): one entry each, `entity_type: "user"`,
  `entity_id` = user id, `actor_id` = acting **user** id: `user.created` ·
  `user.updated` (`changed_fields` names only — email/role/employee_id; a
  role change is recorded here, not a separate action) · `user.activated` ·
  `user.deactivated` · `user.password_changed` (own, current password
  required) · `user.password_reset` (admin). `changes` never contains
  password material. Login/logout are not audited; failed logins are never
  stored.
- **Auth routes** (T1): `LoginRequest {"email", "password"}` → `200 User` +
  `Set-Cookie: wp_session=...`; the same `User` shape is returned everywhere —
  never password material or session data. `GET /auth/me` and
  `PUT /auth/password` resolve the session exactly like every other protected
  route. The `400 invalid_identity` code is **retired** with the dev headers
  (ADR 0004's one-place validation moves into the session resolver): a
  missing or unusable session is always `401 unauthenticated`.
- **Last-admin guard & list order** (T1): `409 last_admin` is a service-level
  check (not a DB constraint); default user list order `email` ascending.
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
- Deactivating the last active admin/super_admin → `409 last_admin`.
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

Technical defaults recorded here, cheap to reverse — **decided in T1** and
pinned as contract surface in **API contract → Pinned details**: session
absolute TTL 7 days, password minimum 10 characters, cookie name `wp_session`,
SHA-256 token lookup hash, `409 last_admin` guard.

## Plan

Planned 2026-09-29. **Contract-first:** the API contract above is already
pinned (2026-09-29); T1 promotes the remaining "proposed" technical defaults
to decided before any code. **No event contracts are written** — Events: none
(decision 5), so rule 4's event-first ordering does not apply. Build order:
persistence (T2) → identity domain core (T3) → auth routes against the pinned
contract (T4) → the atomic identity swap (T5, ADR 0004's "swap the identity
source and nothing else") → user management routes (T6) → web (T7) → record
(T8). `make check` includes `make check-e2e` (since 2026-09-29), so the swap
task must carry the e2e migration inside it: the dev headers stop working the
moment they are deleted. Every task ends with `make check` green.

### Acceptance criteria → where they are satisfied

| # | Criterion (restated) | Changes that satisfy it | Proof |
| --- | --- | --- | --- |
| AC1 | seeded user logs in (`POST /auth/login`) → `200 User`, `wp_session` cookie, `GET /auth/me` returns that user | T2 seed users + models; T3 `AuthService.login/me`; T4 `auth.py` routes + cookie | `tests/integration/test_auth_api.py` |
| AC2 | wrong password and unknown email → identical `401 invalid_credentials` | T3 login failure paths collapse both cases before any password work | `test_auth_api.py` (byte-identical body assert) |
| AC3 | missing/unknown/expired session → `401 unauthenticated` on every protected route | T5 `get_identity` session resolution (fail closed); T4 resolver for `/auth/me` + `/auth/password` | `test_auth_api.py` + T5 sweep of protected routes |
| AC4 | `PUT /auth/password` revokes the pre-change session; new password logs in | T3 `change_password` (verify current → rehash → delete all sessions); T4 route | `test_auth_api.py` (revocation + re-login) |
| AC5 | HR `POST /api/v1/users` → `403 forbidden`; only admin/super_admin manage users | T3 `policies.ensure_can_manage_users`; T6 routes call it | `tests/unit/identity/test_policies.py` · `tests/integration/test_users_roles.py` |
| AC6 | tenant B admin `GET /users/{id}` of a tenant A user → `404 user_not_found`; list never cross-tenant | T2 tenant-scoped `UserRepository`; T6 get/list | `test_users_tenant_isolation.py` |
| AC7 | create user + employee link → `201`; duplicate global email → `409 user_email_taken`; linked employee → `409 employee_link_taken`; other tenant's employee → `404 employee_not_found` | T3 `UserService.create` (global email check, `EmployeeLinkPort`, link-taken check) + T2 unique `email` backstop | `test_users_api.py` · `test_users_failures.py` |
| AC8 | deactivated user cannot log in and old session rejected; `activate` restores | T2 `users.active`; T3 login rejects inactive; T5 resolution rejects inactive user's sessions | `test_auth_api.py` |
| AC9 | user create/update/activate/deactivate/password change/reset each write one audit entry with acting user id, no password values | T3 `UserService`/`AuthService` audit writes (actions pinned in T1; `changes` never carries password material) | `test_users_audit.py` · `test_auth_api.py` |
| AC10 | session linked to employee E: `GET /employees/me` returns E; check-in opens E's record | T5 consumer adaptation (`identity.employee_id` in employees + attendance); T6 creates the linked user under test | `tests/integration/test_auth_consumers.py` |
| AC11 | web login lands on dashboard; wrong credentials show error state; unauthenticated `(dashboard)` visits redirect to login | T7 login page, `middleware.ts` cookie guard + layout 401 guard | `apps/web/tests/auth/*` · `tests/e2e/auth.spec.ts` |
| AC12 | Settings → Users lists users; create/edit/deactivate/reset flows render success + `ApiError` states; unreachable and hidden for non-admins | T7 `features/users/` + `settings/users` page + nav role filtering + page guard | `apps/web/tests/users/*` · `tests/e2e/users.spec.ts` |
| AC13 | bad-credentials submit keeps the user on the page and surfaces `invalid_credentials` | T7 `LoginForm` error state keyed on `ApiError.code` | `apps/web/tests/auth/login-form.test.tsx` + e2e |
| AC14 | e2e: seeded user logs in through the UI and completes a protected flow; dev-identity-headers-only request rejected | T5 e2e bridge (real logins replace header injection) + T7 UI-login specs | `tests/e2e/auth.spec.ts` (incl. headers-only request → 401) |

### T1 — Pin the remaining technical details (docs only) · done 2026-09-29

The routes/shapes/error table above are already pinned. Promote the
"proposed" defaults to decided and pin the rest as contract surface:

- **Sessions**: opaque token = 32 random bytes (`secrets.token_urlsafe`) — the
  256-bit token; the cookie carries the raw token, `sessions.token_hash` stores
  its SHA-256 hexdigest (lookup key only). Absolute TTL **7 days**
  (`expires_at`); expired rows are rejected on read. Cookie `wp_session`:
  `HttpOnly`, `SameSite=Lax`, `Path=/`; `Secure` toggled by a settings flag
  (`SESSION_COOKIE_SECURE`, on outside local dev). Logout deletes the row and
  clears the cookie; logout without a session is `204`.
- **Passwords**: argon2id via argon2-cffi `PasswordHasher` defaults (t=3,
  m=64 MiB, p=4 — ADR 0006 pre-agreed dependency). Hashing/verification is
  sync CPU work and runs off the event loop (`anyio.to_thread`). Minimum 10
  characters, no complexity rules (`validation_error` 422 below). Email is a
  plain string (consistent with employees) — no new dependency.
- **Audit actions** (one entry each, `entity_type: "user"`, `entity_id` = user
  id, `actor_id` = acting **user** id): `user.created` · `user.updated`
  (`changed_fields` names only — email/role/employee_id; a role change is
  recorded here, not a separate action) · `user.activated` · `user.deactivated`
  · `user.password_changed` (own, current password required) ·
  `user.password_reset` (admin). `changes` never contains password material.
  Login/logout are not audited; failed logins are never stored.
- **Auth routes**: `LoginRequest {"email", "password"}` → `200 User` +
  `Set-Cookie: wp_session=...` (same `User` shape everywhere; never password
  material or session data). `GET /auth/me` and `PUT /auth/password` resolve
  the session exactly like every other protected route. The `400
  invalid_identity` code is **retired** with the dev headers; a missing or
  unusable session is always `401 unauthenticated` (ADR 0004's one-place
  validation now lives in the session resolver).
- **Identity object**: rename `DevIdentity` → `Identity`, frozen shape
  `{tenant_id, actor_id /* user id */, role, employee_id /* UUID | None */}`.
  A user with no employee link hitting an employee-scoped flow (`/employees/me`,
  check-in/out, employee self-scope) gets `404 employee_not_found` (or the
  route's existing not-found code), never a server error.
- **Technical defaults made final**: `409 last_admin` guard (service-level;
  the check is not a DB constraint), default user list order `email` ascending,
  `User` shape exactly as pinned above.

Verify: docs-only diff; `make check` still green.

### T2 — Persistence: users + sessions · migration 0004 · credentials helpers · seeds · done 2026-09-29

- `uv add argon2-cffi` to `apps/api` (rule 10: justified by the credential
  business rules; ADR 0006 sanctioned it for Phase 2; `pyjwt` stays unused).
- `apps/api/app/domains/identity/models.py` — ORM (employees pattern, no
  mixins):
  - `User` → `users`: `id` (uuid7 PK), `tenant_id`, `email`, `role` (the six
    values), `employee_id?` (UUID, no FK — denormalized tenant column
    convention), `password_hash`, `active`, `created_at`, `updated_at`.
    Constraints: **unique `email` globally** (decision 7 — unique index on
    `email` alone); index `(tenant_id, active)`.
  - `Session` → `sessions`: `id` (uuid7 PK), `tenant_id`, `user_id` (FK
    `users.id`), `token_hash` (unique), `created_at`, `expires_at`. Indexes:
    `user_id`, `expires_at`.
- `apps/api/app/domains/identity/credentials.py` — pure helpers:
  `hash_password`/`verify_password` (argon2, run via `anyio.to_thread`),
  `new_session_token()` (32 random bytes, url-safe), `session_token_hash(token)`
  (SHA-256 hexdigest).
- `apps/api/app/domains/identity/exceptions.py` (`user_not_found` 404,
  `invalid_credentials` 401, `unauthenticated` 401, `invalid_password` 400,
  `user_email_taken` / `employee_link_taken` / `last_admin` 409 — statuses as
  pinned) and `schemas.py` (`User`, `LoginRequest`, `ChangePasswordRequest`,
  `CreateUser`, `UpdateUser`, `ResetPasswordRequest`, `UserListPage`).
- `apps/api/app/domains/identity/repository.py` — Protocols only:
  `UserRepository` (get, list(filters, page), add, update, `email_taken`
  **globally**, `count_active_admins`), `SessionRepository` (add,
  `get_by_token_hash`, delete, delete_for_user), and `EmployeeLinkPort`
  (`exists(tenant_id, employee_id) -> bool`) — the employees existence check
  goes through a port implemented in infrastructure over `employees`, exactly
  like `PostgresEmployeeUsage` (ADR 0007 precedent): no domain imports another
  domain's tables. Login's get-by-email is deliberately not tenant-scoped (the
  account resolves the tenant) — documented on the Protocol.
- `apps/api/app/infrastructure/postgres/repositories/identity.py` +
  `employee_link.py` adapters (tenant `WHERE` on every tenant-scoped
  statement; `IntegrityError` → `user_email_taken` by constraint name).
- Alembic `apps/api/alembic/versions/0004_identity.py` (append-only;
  autogenerate → review).
- `database/postgres/seeds/003_users.sql` — idempotent (`ON CONFLICT DO
  NOTHING`), synthetic only (`.ai/security.md`): admin user
  `00000000-0000-4000-8000-000000000011` (role `admin`), hr user `...0012`,
  and a dev worker user `...0013` linked to one synthetic employee `...0021`
  (+ minimal `employments` row referencing the seeded 001 organizations data)
  so the seeded worker can check in out of the box — this amends
  `002_attendance.sql`'s "no employee rows" note deliberately. One documented
  dev password (≥10 chars) whose argon2 hash is embedded; plaintext lives in
  the seed header + `.env` comment (synthetic, dev-only).
- `apps/api/tests/integration/conftest.py`: append `users`, `sessions` to
  `ALL_TABLES`.
- Tests: `tests/unit/identity/test_credentials.py` (hash/verify roundtrip,
  wrong password, token hash stability) and
  `tests/integration/test_identity_repository.py` (tenant scoping, global
  email uniqueness across tenants, session lookup + delete_for_user,
  `EmployeeLinkPort`).

Verify: `make check-api` green; `make check-e2e` still green (seeds are
additive; existing specs are untouched).

### T3 — Identity domain core (services + policies) + unit tests · done 2026-09-29

- `apps/api/app/domains/identity/service.py`:
  - `AuthService` (repos + `AuditWriter`, `now` parameters):
    `login` (global email lookup → **one** failure path for unknown email,
    wrong password and deactivated user: `invalid_credentials`, byte-identical
    bodies; verify password off the event loop; create session row; return
    user + raw token), `logout` (delete by token hash — idempotent),
    `me` (resolve session → user), `change_password` (requires current
    password → `invalid_password` 400; rehash; **delete every session** of the
    user; audit `user.password_changed`).
  - `UserService` (repos + `EmployeeLinkPort` + `AuditWriter`):
    `list` (filters `q` = case-insensitive email substring, `role`, `active`;
    `PageParams`; default order email asc), `get` (tenant-scoped → 404),
    `create` (global email uniqueness → `user_email_taken`; employee link
    must exist in the tenant (`employee_not_found`) and be unlinked
    (`employee_link_taken`); hash password; audit `user.created`),
    `update` (`exclude_unset` diffing; email change re-checks global
    uniqueness; explicit `"employee_id": null` unlinks; audit `user.updated`
    with `changed_fields` names), `activate`/`deactivate` (audit each;
    deactivate rejects the **last** active admin/super_admin → `409
    last_admin`), `reset_password` (admin; rehash; **delete every session**;
    audit `user.password_reset`).
- `apps/api/app/domains/identity/policies.py` —
  `ensure_can_manage_users` (admin/super_admin only; `403 forbidden`).
  Login/me/change-password need no policy (authenticated by the session).
- Unit tests `tests/unit/identity/` (`fakes.py` in-memory repos/port/writer;
  `test_auth_service.py`, `test_user_service.py`, `test_policies.py`) covering
  every rule and edge case in this spec: identical failure bodies, revocation
  on change and reset, `last_admin`, global email conflicts, link rules,
  terminated-employee link allowed, PATCH semantics, denied role per mutating
  use case.

Verify: `make check-api` green (pure unit; no DB).

### T4 — Auth router + session resolver + HTTP tests + regeneration · done 2026-09-29

- `apps/api/app/shared/errors.py` — add `UnauthenticatedError` (401
  `unauthenticated`); `InvalidIdentityError` survives until T5 deletes it.
- `apps/api/app/dependencies.py` — `get_current_user` (session cookie →
  `SessionRepository.get_by_token_hash` → user, `401 unauthenticated` on
  missing/unknown/expired session or inactive user) + `AuthServiceDep` /
  `UserServiceDep` providers.
- `apps/api/app/api/v1/auth.py` — the 4 routes as pinned (`login`, `logout`,
  `getCurrentUser`, `changePassword`; thin: no policy call, `_now()`, service),
  `Set-Cookie`/`delete_cookie` with the T1 flags (settings-driven `Secure`);
  register in `apps/api/app/api/v1/__init__.py` (declare `auth.py` before any
  catch-all patterns — flat paths only, no conflicts).
- `apps/api/app/config.py` — `SESSION_COOKIE_SECURE` (default false locally)
  and the 7-day TTL setting.
- Integration tests `tests/integration/test_auth_api.py` (httpx cookie jar =
  the real flow): AC1 happy path incl. `Set-Cookie` flags and `/auth/me`;
  AC2 byte-identical `401 invalid_credentials`; logout idempotent `204`;
  AC4 change-password revocation + re-login; AC8 deactivated login + old
  session rejection + activate restores; AC9 audit rows for password
  change/reset with no password material in `changes`.
- `make gen-api` (contract + client regenerate; drift test green; web
  compiles against the new hooks — unused until T7).

Verify: `make check` green (web and e2e untouched and green; existing API
routes still authenticate via the dev headers).

### T5 — The identity swap (atomic: dependency + consumers + tests + mutator + e2e) · done 2026-09-30

ADR 0004's exit criterion, as one change — headers cannot half-die:

- `apps/api/app/shared/identity.py` — keep `Role`; replace `DevIdentity` with
  `Identity {tenant_id, actor_id /* user id */, role, employee_id: UUID |
  None}`; **delete** `parse_identity`, `get_identity` and the header
  parameters. The FastAPI dependency moves to `app/dependencies.py`
  (`get_identity` resolves `wp_session` → session → user → `Identity`; this is
  also where ADR 0004 always said it lived), re-exported as `IdentityDep`.
  `UnauthenticatedError` on anything unusable. `InvalidIdentityError` and the
  `invalid_identity` code are deleted.
- Consumer adaptation (employee semantics move to `identity.employee_id`;
  audit/event `actor_id=identity.actor_id` sites are unchanged and now carry
  the **user** id):
  - `apps/api/app/domains/employees/service.py` — L111 manager visibility
    compares `employee.manager_id != identity.employee_id`; L115–117 `me()`
    resolves `identity.employee_id` (`None` → `employee_not_found`); L143–145
    manager list scoping uses `identity.employee_id`.
  - `apps/api/app/domains/attendance/service.py` — L157/L254
    `employee_context(tenant_id, identity.employee_id)` (no link →
    `employee_not_found`); L248 self-only check against
    `identity.employee_id`; L393–395 `_visible_employee_ids` self-scope
    (`employee_id=None` → no visible employees); L411–412 manager board via
    `identity.employee_id`.
  - Rename `DevIdentity` → `Identity` across `domains/{organizations,
    employees,attendance}/{service,policies}.py`.
- API test migration (decision 5): unit conftests construct `Identity`
  directly (fix the `seed_actor_employee` pattern: the actor is a user id,
  the employee a separate id); integration `client_factory` gains an identity
  dependency override (`app.dependency_overrides[get_identity]`) and stops
  sending headers (`identity_headers()` deleted); `tests/unit/test_identity.py`
  is rewritten around the session-resolution rules.
- Mutator `packages/api-client/src/mutator/custom-fetch.ts` — delete
  `devIdentityHeaders()` and the `NEXT_PUBLIC_DEV_*` env vars; add
  `credentials: "include"` so the session cookie flows cross-origin (CORS
  already allows credentials, `apps/api/app/main.py`); rewrite
  `apps/web/tests/api/custom-fetch.test.ts` accordingly.
- E2E bridge (API-level login; the UI-login spec lands in T7):
  `tests/e2e/` gains a `login()` helper over `context.request.post(
  '/api/v1/auth/login')` (shares the browser context's cookie jar);
  `ADMIN_HEADERS` and the `impersonate()` header-rewrite helper in
  `attendance.spec.ts` are replaced by admin-created employees + linked users
  logged in for real; all existing specs migrate. Update the `.env` comment
  headers (drop dev-identity vars, document the seeded credentials).

Verify: `make check` green — `check-api`, `check-web` and `check-e2e` all in
the same task (this is the gate the atomicity argument rests on).

### T6 — Users router + HTTP tests + regeneration · done 2026-09-30

- `apps/api/app/api/v1/users.py` — the 7 routes as pinned (`listUsers`,
  `createUser`, `getUser`, `updateUser`, `activateUser`, `deactivateUser`,
  `resetUserPassword`; thin: `IdentityDep` + `ensure_can_manage_users` +
  `_now()` + service); register in `api/v1/__init__.py`.
- Integration tests:
  - `tests/integration/test_users_api.py` — AC7 create with link → `201`,
    exact pinned `User` keys (asserts no password/session material), list
    filters + pagination stability, PATCH semantics (absent = unchanged,
    `employee_id: null` unlinks, `active` never in PATCH), activate/deactivate
    round-trip, reset password.
  - `tests/integration/test_users_failures.py` — `user_email_taken` (incl. an
    email held by **another tenant**), `employee_link_taken`,
    `employee_not_found` (other tenant's employee), `last_admin`, `422
    validation_error` (short password).
  - `tests/integration/test_users_roles.py` — AC5: hr/manager/employee/
    finance → `403 forbidden` on every users route.
  - `tests/integration/test_users_tenant_isolation.py` — AC6: tenant B admin
    get/patch/activate/deactivate/reset of a tenant A user → `404
    user_not_found`; list never leaks.
  - `tests/integration/test_users_audit.py` — AC9 for the users routes
    (`user.created/updated/activated/deactivated/password_reset`), acting
    user id recorded, no password values in any `changes` payload.
  - `tests/integration/test_auth_consumers.py` — AC10 regression: create a
    user linked to employee E via the API, log in for real, `GET
    /employees/me` returns E and `POST /attendance/check-ins` opens E's record.
- `make gen-api`.

Verify: `make check` green.

### T7 — Web: login · logout · auth guard · Settings → Users · e2e UI login · done 2026-09-30

All HTTP via regenerated `@workpulse/api-client` hooks (cookies flow through
the T5 mutator; no hand-written fetch). One naming deviation: the cookie guard
lands in **`apps/web/proxy.ts`**, not `middleware.ts` — Next.js 16 (installed,
ADR 0005) renamed the middleware file and its export to `proxy`; it is the
same cookie-presence guard this section pinned. A pre-commit review pass also
hardened the slice: the `(dashboard)` 401 path signs the stale cookie out
(idempotent logout) before routing to `/login` — without it the
presence-only `/login → /dashboard` proxy branch loops a stale-cookie
visitor forever (regression-tested in e2e); login success clears the whole
query cache (no cross-session bleed); the edit dialog remounts per target
(`key`) so RHF prefill matches the row instead of create defaults; the users
search debounces 300 ms; the employee picker's list query mounts with the
open dialog; the admin-role set and the six-role list each have one web-side
source; reset-dialog failures render the pinned `userFieldErrors` mapping.

- `apps/web/features/auth/` — `api.ts` (query keys + invalidation),
  `hooks/use-auth.ts` (`useCurrentUser` = generated `getCurrentUser`,
  `useLogin`, `useLogout`; `ApiError.code` → static message mapping),
  `components/LoginForm.tsx` (react-hook-form + zod; `invalid_credentials`
  → visible error state, stays on the page). No change-own-password UI in this
  feature (route exists for later; scope lists login/logout/users only).
- Routes: `apps/web/app/(auth)/layout.tsx` + `app/(auth)/login/page.tsx`
  (already-authenticated visitors redirect to `/dashboard`).
- `apps/web/middleware.ts` (new) — cookie-presence guards only: `(dashboard)`
  routes without `wp_session` → `/login`; `/login` with the cookie →
  `/dashboard`. Stale cookies are caught by the layout guard below.
- `apps/web/app/(dashboard)/layout.tsx` — `useCurrentUser` guard: `401` →
  redirect `/login`; exposes the role so nav can filter.
- `apps/web/features/users/` — `api.ts`, `hooks/use-users.ts`,
  `components/UsersTable.tsx` (filters `q`/`role`/`active`), `UserFormDialog`
  (create/edit: email/role/employee link; server errors → `form.setError`
  per `ApiError.code` (`user_email_taken` → email,
  `employee_link_taken`/`employee_not_found` → employee field) + visible
  `rootError`), `DeactivateDialog` (guard-race `last_admin` shown as error),
  `ResetPasswordDialog`.
- `apps/web/app/(dashboard)/settings/users/page.tsx` (+ settings layout if
  needed); nav entry in `components/destinations.ts`, hidden for non-admins in
  `main-nav.tsx` via `useCurrentUser`; direct visits by non-admins redirect to
  `/dashboard` (AC12 "unreachable").
- Web tests `apps/web/tests/auth/*` (login form: happy path, AC13 bad
  credentials error state, redirect when already authed) and
  `apps/web/tests/users/*` (table states; create/edit happy + `409`/`404`
  error states; deactivate confirm; reset dialog).
- E2E: `tests/e2e/auth.spec.ts` — UI login happy → dashboard; bad credentials
  keep the user on `/login` with the `invalid_credentials` message; logout;
  a request carrying only the old dev-identity headers (no session) is
  rejected (AC14's second half). `tests/e2e/users.spec.ts` — admin creates and
  deactivates a user; non-admin cannot see or reach the page. Existing specs
  switch from the T5 `login()` helper to UI login (decision 5's end state);
  the attendance check-in flow runs as the seeded worker user (AC14).

Verify: `make check` green.

### T8 — Record

- `.ai/decisions/0010-authn-sessions-and-identity-swap.md` — ADR (see risks):
  DB-backed opaque sessions over JWT, global email uniqueness + tenant
  resolution from the account, the identity object swap (`actor_id` becomes
  the user id; `employee_id` link) and its audit/event `actor_id` semantic
  change, session-resolver placement in `app/dependencies.py` (resolving ADR
  0004's drift note).
- This spec: `Status: done`, tick every acceptance criterion.
- `tasks/TODO.md`: move the Identity & Access item to Done.
- `.ai/` consistency pass: `domain.md`/`security.md` only if wording drifted
  (the role matrix is unchanged); fix ADR 0004's "dependency lives in
  `app/dependencies.py`" note only insofar as 0010 records where it landed.

Verify: `make check` green; docs consistent (a reviewer can follow the diff
alone).

### Test strategy

| Level | Tests (added/changed) | Gate | Failure cases covered |
| --- | --- | --- | --- |
| Unit | `tests/unit/identity/{test_credentials,test_auth_service,test_user_service,test_policies}.py`; `tests/unit/test_identity.py` rewritten; every domain conftest's identity builder renamed | `make check-api` | identical `invalid_credentials` for unknown email / wrong password / deactivated; revocation on change and reset; `last_admin`; global email uniqueness; link-taken / cross-tenant link; PATCH semantics; password minimum; denied role per mutating use case |
| Integration (repo/API) | `test_identity_repository.py`, `test_auth_api.py`, `test_users_{api,failures,roles,tenant_isolation,audit}.py`, `test_auth_consumers.py`; all existing suites migrate from headers to the `get_identity` dependency override | `make check-api` | AC1–AC9 as named above; cross-tenant users → 404 not 403; expired/unknown/missing session → 401; no password material in responses, errors or audit `changes` |
| Contract | existing OpenAPI drift test via `make gen-api`; pinned `User` key set asserted in `test_users_api.py` (no password/session fields) | `make check-api` | response shapes match the pinned contract; no event contracts needed (decision 5) |
| Web | `apps/web/tests/auth/*`, `apps/web/tests/users/*`; `apps/web/tests/api/custom-fetch.test.ts` rewritten | `make check-web` | AC13 bad-credentials error state; ApiError states on create/edit/deactivate/reset; cookie transport (`credentials: "include"`) |
| E2E | `tests/e2e/auth.spec.ts`, `tests/e2e/users.spec.ts`; `attendance/employees/organizations.spec.ts` migrate to real logins | `make check-e2e` (inside `make check`) | AC11/AC12/AC14 end-to-end; dev-header-only request rejected; seeded worker check-in regression for the `employee_id` swap |

Cross-cutting: every mutating test group asserts its audit entry (rule 3);
`.ai/security.md` PII rules proven by the audit/no-password-material
assertions; the identical-body assertion proves the no-enumeration rule.

### Risks · unknowns · ADR candidates

1. **`SameSite=Lax` vs deployment topology** — the cookie flows because
   localhost:3000 and localhost:8000 are same-site (ports don't matter to
   SameSite). A split-origin deployment on *different* registrable domains
   would need `SameSite=None; Secure` (and an explicit CORS origin). Worth
   checking before any real deployment; Phase 8 owns deploy topology.
2. **No rate limiting on login** — brute force is possible until Phase 7's
   rate limiting/lockout lands. Argon2 (≈50–100 ms/verify) is the only brake.
   Accepted explicitly (out of scope); keep the roadmap item visible. Bodies
   are byte-identical across unknown email / wrong password / deactivated
   (pinned), but unknown emails skip the argon2 verify, so response timing
   still distinguishes them — a dummy-hash verify for unknown emails is the
   cheap follow-up if the threat model tightens (T3, verified).
3. **Audit/event `actor_id` semantics change** — pre-swap audit rows carry
   employee ids, post-swap rows carry user ids, in the same column; the
   attendance event envelopes' `actor_id` likewise (shapes unchanged — no
   contract edit). Recorded in ADR 0010 so the future audit-log read API and
   Phase 3 consumers treat `actor_id` as "authenticated user id".
4. **T5 is a large atomic review unit** (dependency + consumer adaptation +
   all API tests + mutator + e2e). Mitigation: T2–T4 land every reusable part
   first; T5 is mostly mechanical renames and one semantic switch.
5. **Global email uniqueness leaks account existence to admins** (`409
   user_email_taken`) — decision 7 accepts this for admins; unauthenticated
   callers still see identical `401 invalid_credentials` bodies.
6. **Session rows accumulate** — expired sessions are rejected but never
   reaped; a cleanup job belongs with Phase 3 workers. `sessions.expires_at`
   is indexed so a future purge is cheap.
7. **Seed credentials are checked in** — synthetic, dev-only, documented in
   the seed header; consistent with `.ai/security.md`'s synthetic-seeds rule.
   The seeded worker needs one synthetic employee row, amending
   `002_attendance.sql`'s "no employee rows" convention (T2).
8. **`last_admin` is a service-level check** — two concurrent deactivations
   can both pass it (accepted Phase 2 race; a DB-constrainable form would need
   a trigger). Same class of accepted race as `employee_code` generation
   (employees Risk 3).
9. **`get_identity` moves to `app/dependencies.py`** — fixes the ADR 0004
   drift note (it documents that location already); the shared module keeps
   the types so domain imports stay acyclic. No behavior change beyond the
   swap itself.
10. **ADR candidate (T8): ADR 0010** — DB-backed opaque sessions over JWT
    (decision 1; ADR 0006's pyjwt stays unused), global email uniqueness +
    tenant resolution from the account (decision 7), identity-object swap and
    its `actor_id` semantics. Argon2-cffi needs no ADR (ADR 0006 pre-agreed).
11. **Employee-link uniqueness is a service-level check** — T2 pinned no unique
    index on `users.employee_id`, so two concurrent creates/updates can link
    one employee to two users (same accepted Phase 2 race class as Risk 8,
    `employee_link_taken` 409 in the normal path — noted by T3's verifier).
    A partial unique index on `(tenant_id, employee_id) WHERE employee_id IS
    NOT NULL` is the DB backstop when it matters.
