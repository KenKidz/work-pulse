# PostgreSQL

Source of truth for transactions. Two databases, both defined in
`docker-compose.yml`:

| Service | Port | Database | Purpose |
| --- | --- | --- | --- |
| `postgres` | 5432 | `workpulse` | local dev (volume-backed) |
| `postgres-test` | 5433 | `workpulse_test` | throwaway test DB (tmpfs) for `make check-api` |

The connection strings live in `DATABASE_URL` / `DATABASE_TEST_URL`
(`.env.example`).

| Directory | Purpose |
| --- | --- |
| `seeds/` | deterministic dev data, applied in filename order |
| `scripts/` | maintenance scripts (backup, restore, diagnostics) |

## Seeding

```sh
make db-up                # start postgres (+ postgres-test)
make db-seed              # apply every seeds/*.sql — idempotent, re-runnable
```

Seeds use fixed UUIDs and `INSERT … ON CONFLICT DO NOTHING`, so a second run
is a no-op. The dev tenant is `00000000-0000-4000-8000-000000000001` and the
dev actor `00000000-0000-4000-8000-000000000002` — the same defaults the web
app sends as dev-identity headers (ADR 0004). Synthetic data only, never real
people (`.ai/security.md`).

Schema changes are managed by Alembic migrations under `apps/api/alembic/`.
Do not create tables by hand — seeds assume `alembic upgrade head` has run
(`make dev-api` does not migrate; run `cd apps/api && uv run alembic upgrade
head` once after `make db-up`).
