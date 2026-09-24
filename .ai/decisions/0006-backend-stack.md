# 6. Backend stack for `apps/api`

- **Status:** Accepted
- **Date:** 2026-09-24

## Context

`apps/api` runs FastAPI on uv, but everything below the framework was open: no
data-access layer, no migrations, no lint/format, no type checker, no logging or
ID strategy, no test tooling — only the domain-module pattern and the tenant
rules exist (`.ai/conventions.md`, `.ai/security.md`). The roadmap heads toward
realtime dashboards, a transactional outbox and background workers (Phases 2–3),
so the concurrency style must be decided on day one. Repo rules: no dependency
without a job, domains never import infrastructure clients directly,
`make check` is the gate.

## Decision

| Concern | Choice |
| --- | --- |
| Data access | **SQLAlchemy 2.0 async** — typed declarative models (`Mapped[...]`), `asyncpg` driver |
| Migrations | **Alembic** (async template) — constraint naming convention, append-only history |
| Schemas | **Pydantic v2** (ships with FastAPI) — `schemas.py` is the wire contract, `models.py` is persistence |
| Lint + format | **Ruff** (`ruff check` + `ruff format`) — replaces black/isort/flake8 |
| Types | **Pyright**, standard mode — gates `make check-api` |
| Logging | **structlog** — pretty console in dev, JSON in prod; `request_id` and `tenant_id` bound per request; never PII |
| IDs | **UUIDv7** via `uuid-utils` — time-ordered UUIDs in PostgreSQL `uuid` columns |
| Tests | **pytest + pytest-asyncio + httpx** — `AsyncClient(ASGITransport)` for in-process router tests against the `postgres-test` compose service; deterministic hand-rolled fixtures, no factory library |
| DI | **Plain FastAPI `Depends`** — session-per-request; services constructed in `app/dependencies.py`; no DI-container library |
| Concurrency | **Full async** — `async def` from router → service → repository; sync-only libraries go through `anyio.to_thread` with a justification |
| Time | **stdlib `datetime` (UTC) + `zoneinfo`** — `timestamptz` columns; no arrow/pendulum; clock injected, never `datetime.now()` in business logic |
| Outbound HTTP | **httpx** |

Phase-gated libraries — pre-agreed, installed only with their phase (each still
needs its feature spec): Phase 2 `redis` (redis-py async) + FastAPI-native
WebSockets + `pyjwt` + `argon2-cffi` · Phase 3 `aiokafka` · Phase 4
`clickhouse-connect` · Phase 6 OpenTelemetry SDK + instrumentations.

## Consequences

- The dependencies install with the first persistence feature (Organizations) —
  this ADR is the justification rule 10 requires; nothing is added before a
  feature uses it.
- `make check-api` becomes `ruff check` + `ruff format --check` + `pyright` +
  `uv run pytest`.
- ORM models never cross the router boundary: routers accept and return Pydantic
  schemas only; repositories are the only code touching SQLAlchemy.
- The dev dependency `httpx2` was wrong — the confirmed test stack is `httpx`.
- Alembic migrations are append-only: never edit a committed revision;
  autogenerate → review → meaningful revision message; one migration per feature
  where practical.
- UUIDv7 everywhere: index-friendly inserts, no volume leakage across tenants
  (vs BIGSERIAL), safe to expose in URLs and events.
- structlog keeps Phase 6 cheap — OTel trace/log correlation attaches to the
  structured context without a rewrite.
- Reversal cost is moderate: the repository pattern shields domains from the
  ORM, but `Mapped[]` models and migration history make a later swap expensive —
  which is why this decision gets an ADR.
