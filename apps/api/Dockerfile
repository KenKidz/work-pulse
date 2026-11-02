# syntax=docker/dockerfile:1

# One Python image for the API and all five workers (deployment business rule
# 1, ADR 0026). The API is the default command; each worker is the same image
# with a command override (`python -m workers.<name>.main`).
#
# Build from the repository root:
#   docker build -f apps/api/Dockerfile -t workpulse-api:local .

FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Pinned to the version that produced apps/api/uv.lock.
RUN pip install --no-cache-dir uv==0.11.24

WORKDIR /app/apps/api

# Dependencies first so application edits reuse the cached sync layer. The
# project itself is not a package (`[tool.uv] package = false`).
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source, preserving the layout used by `make dev`:
# `app` lives under apps/api, `workers` under the repository root.
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./
COPY apps/api/scripts ./scripts
COPY workers /app/workers

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Production by default: enables JSON logs and disables FastAPI debug
    # tracebacks (the house error handlers own deployed error responses).
    ENVIRONMENT=production \
    PYTHONPATH=/app:/app/apps/api \
    PATH=/app/apps/api/.venv/bin:$PATH \
    # Strict boot configuration is a deployment-only contract: missing
    # connection keys exit 1 with `missing configuration: <KEY>` and never a
    # value. Local dev, tests and compose leave the flag unset.
    WORKPULSE_STRICT_CONFIG=1

WORKDIR /app/apps/api

# Non-root runtime (deployment business rule 1).
RUN groupadd --system --gid 10001 workpulse \
    && useradd --system --uid 10001 --gid workpulse --home-dir /app workpulse

COPY --from=builder --chown=workpulse:workpulse /app/apps/api/.venv ./.venv
COPY --from=builder --chown=workpulse:workpulse /app/apps/api/app ./app
COPY --from=builder --chown=workpulse:workpulse /app/apps/api/alembic ./alembic
COPY --from=builder --chown=workpulse:workpulse /app/apps/api/alembic.ini ./
COPY --from=builder --chown=workpulse:workpulse /app/apps/api/scripts ./scripts
COPY --from=builder --chown=workpulse:workpulse /app/workers /app/workers
# The ClickHouse DDL is read at runtime from the repo-root-relative path that
# `app/infrastructure/clickhouse/schema.py` resolves; without it the CDC sink
# cannot apply its tables and the API's best-effort schema startup always fails.
COPY --chown=workpulse:workpulse database/clickhouse/schema.sql /app/database/clickhouse/schema.sql

USER workpulse

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
