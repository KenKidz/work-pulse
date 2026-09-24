# WorkPulse API — local rules

FastAPI modular monolith. The root `/AGENTS.md` rules still apply.

## Commands

| Command | Purpose |
| --- | --- |
| `uv sync` | install/refresh dependencies |
| `uv run uvicorn app.main:app --reload` | dev server on :8000 |
| `uv run pytest` | test suite — the API gate of `make check` |
| `uv add <package>` | add a dependency (justify it in the feature spec first) |

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

- Routers stay thin: parse → call a domain service → return a schema.
- Business logic lives only in `domains/<name>/service.py`.
- Domains never import infrastructure clients directly.
- Errors subclass `app.shared.errors.WorkPulseError` with a stable code.
- Every new domain starts from a spec in `.ai/features/`.
- Never log sensitive employee data; always scope data by `tenant_id`.