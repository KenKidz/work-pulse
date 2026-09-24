# 4. Multi-tenant from day one; Phase 1 identity via dev headers

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

WorkPulse holds per-company, highly sensitive workforce data. Two forks had to be
settled before the first table is created, because both are expensive to
retrofit:

1. Whether the schema carries `tenant_id` from the very first migration.
2. Whether Phase 1 builds real authentication, or something smaller that still
   exercises authorization.

Phase 1 ships Organizations, Employees and Attendance. Building authN first would
delay all three; building them with no identity at all would mean policies,
denied-role tests and audit actors all get retrofitted later.

## Decision

- **Multi-tenant from day one.** Every table and every event carries `tenant_id`;
  scoping is enforced in repositories, never in routers; each domain ships a
  tenant-isolation test. One dev tenant is seeded for local work.
- **Phase 1 identity is a dev identity**: `X-Tenant-Id`, `X-Actor-Id`, `X-Role`
  headers validated by a single dependency in `app/dependencies.py`. Domain
  policies consume that identity exactly as they will consume a real session, so
  Phase 2 (authN + RBAC) swaps the identity source and nothing else.
- Cross-tenant reads return **404**, not 403 — existence is not leaked.
- Audit entries record the actor from day one: the dev identity in Phase 1, the
  authenticated user from Phase 2.

## Consequences

- No tenancy migration, backfill, or re-tested isolation later — the hardest
  schema decision is already right in the first migration.
- Authorization code, denied-role tests and audit actors exist from the first
  feature, so Phase 2 is an identity swap rather than a rewrite.
- **Phase 1 endpoints are not safe to expose.** The dev-identity dependency must
  be replaced by real authN before any deployment; that is a Phase 2 exit
  criterion, not a nice-to-have.
- Header-based identity is a temporary trust boundary, so it must be validated in
  exactly one place and never re-derived inside a router or domain.