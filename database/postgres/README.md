# PostgreSQL

Source of truth for transactions. One database, defined in `docker-compose.yml`
(service `postgres`); the connection string lives in `DATABASE_URL`.

| Directory | Purpose | Status |
| --- | --- | --- |
| `seeds/` | deterministic dev/test data | empty — arrives with the first feature (Employees) |
| `scripts/` | maintenance scripts (backup, restore, diagnostics) | empty |

Schema changes are managed by Alembic migrations under `apps/api/alembic/` —
added with the first persistence feature. Do not create tables by hand.