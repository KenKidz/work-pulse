# 2. Monorepo with a FastAPI modular monolith

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

Greenfield project. The product — real-time workforce management and people
analytics — will eventually need event streaming, realtime push, and analytical
storage. Vibe coding requires predictable locations: an agent must be able to go
from a feature name to every surface (domain, router, contract, worker, web
feature, tests) without guessing. Standing up microservices first would multiply
operational cost before a single requirement exists.

## Decision

- One repository, two apps: `apps/web` (Next.js + TypeScript, App Router) and
  `apps/api` (FastAPI, domain-first modular monolith). Python environments and
  dependencies via **uv**.
- PostgreSQL is the only datastore in Phase 1.
- Redis, Kafka, ClickHouse, CDC, Kubernetes are deliberately deferred; each is
  adopted in its own phase with its own spec + ADR (see the roadmap in
  `.ai/architecture.md`).
- `make check` (API tests + web typecheck) is the single verification entry
  point for humans and agents.

## Consequences

- One feature is one vertical slice: domain, router, contract, feature folder,
  tests — all discoverable by name.
- Cross-domain refactors stay cheap inside one deployable.
- AI agents get stable, conventional locations instead of a search problem.
- Domain boundaries are enforced by convention (import rules in `AGENTS.md`),
  not by the network — until an extraction is ever justified.
- A monorepo needs explicit per-surface gates: `make check-api`,
  `make check-web`.