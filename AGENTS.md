# WorkPulse — Agent & Contributor Guide

Single source of truth for humans and coding agents. Read this before making
changes; update it when conventions change.

> Detailed context lives in `.ai/` — this file stays short on purpose.
> `.ai/product.md` (what we build) · `.ai/domain.md` (contexts, language, events,
> state machines) · `.ai/architecture.md` (how it fits together + phase roadmap) ·
> `.ai/conventions.md` (code conventions) · `.ai/security.md` (roles, tenancy,
> PII, audit) · `.ai/testing.md` (gates + test rules) · `.ai/decisions/` (ADRs) ·
> `.ai/features/<feature>.md` (one spec per feature) · `tasks/doc.md` (original brief)

## Product

WorkPulse — **real-time workforce operations & intelligence platform**: every
workforce fact is an event that is visible live, auditable and analysable.
Phase 1 slice: **Organizations → Employees → Attendance**.

## Stack

Installed so far:

- Frontend: Next.js (App Router) + TypeScript strict, Tailwind CSS v4 + shadcn/ui,
  TanStack Query, orval-generated `@workpulse/api-client`, react-hook-form + zod,
  Zustand, dayjs, next-intl (en + vi), Recharts (Reports charts only) —
  `apps/web/` (ADR 0005)
- Backend: FastAPI + SQLAlchemy 2.0 (async) + Alembic, managed by uv —
  `apps/api/` (ADR 0006)
- AuthN: argon2id passwords + DB-backed opaque sessions (`wp_session` cookie),
  session-resolved identity (ADR 0010)
- Datastore: PostgreSQL (source of truth, docker compose) — `database/postgres/`;
  ClickHouse (Phase 4 analytical projection, ADR 0019) — `database/clickhouse/`

Requires Python 3.12+, Node >= 20, pnpm, Docker, and uv.

Later phases — **do not add these without a spec + ADR**: Redis + WebSocket (2),
Kafka + outbox + workers (3), ClickHouse + payroll (4),
CDC (5), observability (6), load/reliability (7), Kubernetes + CI/CD (8).
Roadmap: `.ai/architecture.md`.

## Quickstart

| Command | Purpose |
| --- | --- |
| `make install` | install API (uv) + web (pnpm workspaces) dependencies |
| `make dev` | run everything for browser testing (infra + migrate + seed + API :8000 + web :3000 + workers; Ctrl+C stops) |
| `make db-up` / `make db-down` | start / stop PostgreSQL |
| `make db-test-up` | start the throwaway test PostgreSQL on :5433 |
| `make redis-up` | start Redis (realtime fan-out, :6379; `make redis-test-up` for :6380) |
| `make db-migrate` | apply Alembic migrations to the dev database |
| `make db-seed` | seed the dev database (idempotent) |
| `make dev-api` | FastAPI dev server on :8000 |
| `make dev-web` | Next.js dev server on :3000 |
| `make gen-api` | regenerate OpenAPI + typed `@workpulse/api-client` |
| `make check` | all gates — run before claiming done |
| `make check-e2e` | Playwright e2e gate (migrate + seed + dev servers) — part of `make check` |

## Repo map

```
AGENTS.md             this file
Makefile              single entry point (install / dev / db / check)
docker-compose.yml    PostgreSQL, Redis, Kafka and ClickHouse for local dev
.ai/                  product, domain, architecture, conventions, security,
                      testing, decisions/, features/ (specs + backlog index)
apps/web/             Next.js app (routes in app/, feature logic in features/)
apps/api/             FastAPI app (routers in app/api/v1/, logic in app/domains/)
workers/              background consumers (Phase 3)
packages/api-client/  orval-generated typed client (`make gen-api`)
contracts/            versioned event schemas + generated OpenAPI
database/             postgres/ (seeds, scripts) · clickhouse/ (projection DDL)
scripts/              dev orchestration (`make dev`)
tests/e2e/            cross-surface tests
tasks/                TODO.md (backlog) · doc.md (original brief)
.kilo/                agent config (/spec, /plan, /check commands; researcher, verifier
                      agents; workpulse-ui skill)
```

## CodeGraph

This repo is indexed by CodeGraph (`.codegraph/` at the repo root). For questions
about where or how code works, reach for it BEFORE grep/find or reading files:

- MCP tool (when available): `codegraph_explore` answers most code questions in
  one call — verbatim line-numbered source, call paths, blast radius.
- Shell (always works): `codegraph explore "<symbol names or question>"`.

Treat returned source as already read — do not re-verify with grep. CodeGraph is
navigation, not proof: `make check` and tests still decide correctness. If the
index is missing, skip CodeGraph and use Glob/Grep/Read; never run `codegraph
init` yourself.

## Architecture rules

1. Business logic lives in `apps/api/app/domains/<domain>/`.
2. API routers translate HTTP ↔ application operations; they stay thin.
3. Domain code must not import infrastructure clients (Postgres/Kafka/Redis/
   ClickHouse) directly — repositories wrap I/O.
4. Events have versioned contracts under `contracts/events/`; write the contract
   before the producer or the consumer.
5. Every business feature needs tests: happy path + at least one failure case,
   on every surface it touches (`.ai/testing.md`).
6. Tenant isolation is mandatory (`tenant_id` on every row and event), enforced
   in repositories and proven by a test per domain.
7. Never log sensitive employee data (`.ai/security.md`).
8. Frontend: `app/` holds routes only; feature logic lives in `features/<name>/`.
9. Prefer existing abstractions over new ones; do not create a domain, route,
   or component "for later".
10. Do not introduce a dependency without justifying it in the feature spec.
11. Do not add Phase 2+ technology ahead of its phase.
12. Access checks live in `domains/<name>/policies.py` — never in routers, never
    only in the UI. Cross-tenant reads return 404, not 403.
13. Sensitive mutations write an audit entry (append-only, no PII values).
14. A spec is not `approved` while its `## Decisions needed` section is open —
   ask, do not guess.
15. Frontend UI work (components, pages, styling in `apps/web/`) loads the
    project skill `workpulse-ui` (`.kilo/skills/workpulse-ui/SKILL.md`) and
    follows it: WorkPulse design identity, presentation rules, realtime/live
    surfaces, states, motion, copy, accessibility floor and pre-flight. It is
    the only UI skill required for product work — it already distills
    `ui-ux-pro-max`, `frontend-design`, `design-taste-frontend-v1` and
    `web-design-guidelines`, so those are not loaded for routine UI. shadcn/ui
    work (components in `components/ui/`, adding/fixing primitives) also loads
    the `shadcn` skill. Escalate to an upstream skill only where `workpulse-ui`
    says to: one targeted `ui-ux-pro-max` search for a concern it does not
    cover, `frontend-design` for genuinely new art direction,
    `web-design-guidelines` + `ui-ux-pro-max --domain ux` for a formal UI
    review/audit, and `design-taste-frontend` (v2) instead of it for
    marketing/landing surfaces.

## Vibe-coding workflow

ONE FEATURE → ONE SPEC → ONE DOMAIN → ONE API FLOW → ONE EVENT FLOW → ONE TEST FLOW

1. **Spec** — `/spec <feature>` creates `.ai/features/<feature>.md` from the
   template. No code without a spec. If the spec is silent on a behavior, ask;
   never invent product scope.
2. **Plan** — `/plan .ai/features/<feature>.md` produces an ordered, file-level
   plan inside the spec (only once decisions are answered). The main model
   plans; subagents are used only for read-only repo lookups (`researcher`)
   and verification (`verifier`).
3. **Build** — smallest possible diff, one concern per change, follow the plan.
4. **Verify** — `/check` (runs `make check`); for risky changes use the
   `verifier` subagent. Fix failures before reporting done.
5. **Record** — update the feature spec status, tick `tasks/TODO.md`, and add an
   ADR under `.ai/decisions/` for any decision that is expensive to reverse.

Full-stack changes pin the API contract in the spec first; the plan lands the
backend first and the frontend against it. The surfaces must never drift.

## Definition of done

- Acceptance criteria in the feature spec are met and ticked.
- `make check` passes.
- Tests cover the new behavior on every surface it touches, including tenant
  isolation and a denied-role case for anything mutating.
- Sensitive mutations are audited; no PII in logs, errors or event payloads.
- Docs updated in the same change: feature spec status, `tasks/TODO.md`, and
  this file / `.ai/` when conventions changed.
- A reviewer (human or `verifier` subagent) could understand the change from
  the diff alone.