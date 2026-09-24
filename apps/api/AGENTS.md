# WorkPulse API — local rules

FastAPI modular monolith. The root `/AGENTS.md` rules still apply.

## Commands

| Command | Purpose |
| --- | --- |
| `uv sync` | install/refresh dependencies |
| `uv run uvicorn app.main:app --reload` | dev server on :8000 |
| `uv run pytest` | test suite |
| `uv run ruff check .` / `uv run ruff format .` | lint / format (ADR 0006) |
| `uv run pyright` | type gate (standard mode, ADR 0006) |
| `uv add <package>` | add a dependency (justify it in the feature spec first) |

`make check-api` runs Ruff + Pyright + pytest — all three must pass.

## Layout

- `app/main.py` — app factory, middleware, `/healthz`
- `app/config.py` — env-driven settings (pydantic-settings)
- `app/dependencies.py` — shared FastAPI providers (settings, future DB session)
- `app/api/v1/` — thin routers, one module per domain
- `app/domains/<name>/` — business logic (see `app/domains/README.md`)
- `app/infrastructure/` — I/O adapters (see `app/infrastructure/README.md`)
- `app/shared/` — cross-domain plumbing (errors, logging, pagination)
- `tests/unit` | `tests/integration` | `tests/contract`

## Rules

- Stack is pinned by ADR 0006: SQLAlchemy 2.0 async + asyncpg · Alembic ·
  Pydantic v2 · structlog · UUIDv7 ids (`uuid-utils`) · Ruff · Pyright ·
  pytest + pytest-asyncio + httpx. Phase-gated libraries install with their
  phase, never before.
- Routers stay thin: parse → call a domain service → return a schema. ORM
  models never cross the router boundary.
- Business logic lives only in `domains/<name>/service.py`.
- Full async: `async def` from router → service → repository.
- Domains never import infrastructure clients directly.
- Errors subclass `app.shared.errors.WorkPulseError` with a stable code.
- Migrations are append-only: never edit a committed Alembic revision.
- Every new domain starts from a spec in `.ai/features/`.
- Never log sensitive employee data; always scope data by `tenant_id`.