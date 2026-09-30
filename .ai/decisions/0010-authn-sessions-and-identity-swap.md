# 10. AuthN: DB-backed opaque sessions and the identity swap

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

ADR 0004 gave Phase 1 a dev identity (`X-Tenant-Id`, `X-Actor-Id`, `X-Role`) and
promised that Phase 2 would "swap the identity source and nothing else" before
any deployment. `.ai/features/identity-access.md` (T1–T8, 2026-09-29 →
2026-09-30) delivered that swap: real accounts, argon2id credentials, DB-backed
sessions, session-resolved identity, and user management. Three choices in it
are expensive to reverse — the session mechanism, how a login names its tenant,
and what `actor_id` now means in audit rows and event envelopes.

Alternatives considered:

- **JWT access tokens (ADR 0006's pre-agreed `pyjwt`).** Stateless, but
  revocation needs a denylist or Redis — new infra before Redis earns its
  place, and logout/password-reset must invalidate *now*, not at expiry.
- **Self-registration / invite emails.** Needs email delivery (Phase 3
  Notifications); the product provisions accounts through admins instead.

## Decision

- **Sessions are opaque, DB-backed.** Token = 32 random bytes
  (`secrets.token_urlsafe`); the `wp_session` cookie carries the raw token,
  `sessions.token_hash` stores its SHA-256 hexdigest as a lookup key only.
  Absolute TTL 7 days (`expires_at`, indexed); logout deletes the row; any
  credential change revokes every session of that user. Cookie is `HttpOnly`,
  `SameSite=Lax`, `Path=/`, `Secure` outside local dev
  (`SESSION_COOKIE_SECURE`). `pyjwt` stays unused.
- **Passwords are argon2id** (argon2-cffi — pre-agreed in ADR 0006, no further
  ADR needed), minimum 10 characters, hashed/verified off the event loop.
  Password material never appears in responses, errors, logs or audit entries.
- **User emails are unique globally; login resolves the tenant from the
  account** (`POST /auth/login {email, password}`). One account belongs to
  exactly one tenant, so the client never names a tenant — cross-tenant account
  takeover by header is structurally impossible.
- **The identity object swaps its source and keeps its shape.** `DevIdentity`
  becomes `Identity {tenant_id, actor_id /* user id */, role, employee_id:
  UUID | None}`; the dev headers are **deleted**, not flagged, and
  `400 invalid_identity` is retired — a missing/unusable session is always
  `401 unauthenticated`. Employee-scoped flows resolve through
  `identity.employee_id` (no link → `404 employee_not_found`).
- **`actor_id` semantics change.** From this swap on, audit entries' and event
  envelopes' `actor_id` carries the authenticated **user** id (previously the
  employee id). Shapes are unchanged — no contract edit — but the column
  straddles two meanings across the migration date.
- **The session resolver is the single trust boundary and lives in
  `app/dependencies.py`** (`get_current_user` / `get_identity` /
  `IdentityDep`) — the location ADR 0004 documented, ending the Phase 1 drift
  where the parser sat in `app/shared/identity.py`. That module keeps the
  `Role`/`Identity` types so domain imports stay acyclic. Authorization itself
  stays in `domains/<name>/policies.py`.
- **User management is admin/super_admin only** (`ensure_can_manage_users`).
  Roles stay the fixed six (`.ai/security.md` matrix unchanged); the last
  active admin cannot be deactivated (`409 last_admin`, service-level check).

## Consequences

- ADR 0004's exit criterion is met: nothing trusts request headers, and the
  auth boundary is deployable. Sessions re-resolve user + role per request, so
  role changes and deactivation take effect immediately.
- The audit log's `actor_id` must be read as "authenticated user id" from
  2026-09-30 on; pre-swap rows carry employee ids. The future audit-log read
  API and Phase 3 event consumers treat `actor_id` as the user id.
- Expired session rows are rejected but not yet reaped — a cleanup job belongs
  to Phase 3 workers (`expires_at` is indexed so a purge is cheap).
- Accepted Phase 2 races: `last_admin` and the one-employee-one-user link check
  are service-level, not DB constraints; concurrent requests can slip both.
- Login rate limiting/lockout is Phase 7; argon2 verify is the only brake, and
  unknown emails skip it (timing oracle) — a dummy-hash verify is the cheap
  follow-up if the threat model tightens.
- `SameSite=Lax` assumes a same-site deployment (localhost ports don't matter).
  A split-registrable-domain topology needs `SameSite=None; Secure` + an
  explicit CORS origin — check before real deployment (Phase 8 owns topology).
- Seed accounts (`database/postgres/seeds/`) are synthetic and dev-only, per
  `.ai/security.md`'s fixtures rule.
