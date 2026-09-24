# TODO

Working backlog. Keep it short — anything larger than a task belongs in a feature
spec under `.ai/features/`. Move items between sections in the same change that
does the work.

## Now

- [ ] `/plan .ai/features/organizations.md` → build → `make check`
- [ ] Then `employees.md`: plan → build
- [ ] Then `attendance.md`: plan → build
- [ ] Phase 1 exit criteria (`.ai/product.md`): all three features green end-to-end

## Next

- [ ] Persistence foundation (lands with Organizations — tooling per ADR 0006: SQLAlchemy 2.0 async + asyncpg, Alembic, Ruff, Pyright, structlog, uuid-utils, pytest-asyncio, httpx): engine/session in `apps/api/app/infrastructure/postgres/`, Alembic under `apps/api/alembic/`, plus a `postgres-test` service in `docker-compose.yml`
- [ ] Dev-identity dependency (`X-Tenant-Id`, `X-Actor-Id`, `X-Role`) in `app/dependencies.py` — ADR 0004
- [ ] Audit log: append-only table + writer in `apps/api/app/shared/` (required by Employees)
- [ ] Seed data in `database/postgres/seeds/` (one dev tenant, synthetic people only)
- [ ] Playwright in `tests/e2e/`, wired as `make check-e2e` (Vitest landed with ADR 0005)
- [ ] Re-run `make gen-api` when the first endpoint group lands (pipeline + spec exist)
- [ ] Commit the scaffold — the repo has no commits yet

## Later

- [ ] Phase 2: Identity & Access (authN + RBAC — **must replace the dev-identity headers before any deployment**, ADR 0004) · Realtime Command Center (Redis + WebSocket)
- [ ] Phase 3: Kafka + transactional outbox + workers · Leave · Notifications
- [ ] Phase 4: ClickHouse analytics · Payroll (simulated, ADR 0003) · Recruitment · Performance · Documents
- [ ] Phase 5: CDC / Debezium
- [ ] Phase 6: OpenTelemetry / Prometheus / Grafana
- [ ] Phase 7: load testing, rate limiting, circuit breakers
- [ ] Phase 8: Docker images, Kubernetes, CI/CD

## Done

- [x] Bootstrap repo: AGENTS.md, docs/, specs/, tasks/, .kilo/ config, runnable placeholder
- [x] Adopt the real stack: monorepo (`apps/web`, `apps/api`), `.ai/` context, contracts, database, e2e (ADR 0002)
- [x] Product definition from the brief (`tasks/doc.md`): `product.md`, `domain.md`, `security.md`, `testing.md`, 8-phase roadmap, ADR 0003
- [x] Draft specs for the Phase 1 slice: organizations, employees, attendance
- [x] Product decisions resolved with the owner: tenancy + Phase 1 identity (ADR 0004), attendance scope, test tooling
- [x] Frontend stack defined and configured (ADR 0005): Tailwind v4 + shadcn/ui, TanStack Query, orval-generated `@workpulse/api-client`, RHF + zod, Zustand, dayjs, ESLint + Prettier; Vitest wired into `make check-web`; pnpm workspaces at the repo root
- [x] Backend stack defined (ADR 0006): SQLAlchemy 2.0 async + asyncpg + Alembic, Pydantic v2, Ruff + Pyright, structlog, UUIDv7 ids, pytest + pytest-asyncio + httpx against `postgres-test`; full-async rule; phase-gated libraries pre-agreed (redis, pyjwt/argon2-cffi, aiokafka, clickhouse-connect, OTel); `httpx2` dev-dep fixed to `httpx`