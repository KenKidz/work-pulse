.DEFAULT_GOAL := help

API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: help install dev db-up db-down db-test-up redis-up redis-test-up kafka-up kafka-test-up clickhouse-up clickhouse-down clickhouse-test-up minio-up minio-test-up observability-up observability-down db-migrate db-seed dev-api dev-web dev-relay dev-realtime dev-notifications dev-payroll dev-reports check check-api check-web check-e2e gen-api format-web

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install API (uv) and web (pnpm workspaces) dependencies
	cd $(API_DIR) && uv sync
	pnpm install

db-up: ## Start PostgreSQL (detached, waits for healthy)
	docker compose up -d --wait postgres

db-test-up: ## Start the throwaway test PostgreSQL on :5433 (detached)
	docker compose up -d --wait postgres-test

redis-up: ## Start Redis (realtime fan-out, :6379; detached, waits for healthy)
	docker compose up -d --wait redis

redis-test-up: ## Start the throwaway test Redis on :6380 (detached)
	docker compose up -d --wait redis-test

kafka-up: ## Start Kafka (event bus, :9092; detached, waits for healthy)
	docker compose up -d --wait kafka

kafka-test-up: ## Start the throwaway test Kafka on :9093 (detached)
	docker compose up -d --wait kafka-test

clickhouse-up: ## Start ClickHouse (analytics projection, :8123; detached, waits for healthy)
	docker compose up -d --wait clickhouse

clickhouse-test-up: ## Start the throwaway test ClickHouse on :8124 (detached)
	docker compose up -d --wait clickhouse-test

clickhouse-down: ## Stop ClickHouse (the dev data volume is kept)
	docker compose stop clickhouse

minio-up: ## Start MinIO (document storage, :9000; bucket provisioned)
	docker compose up -d --wait minio
	docker compose run --rm minio-init

minio-test-up: ## Start the throwaway test MinIO on :9002 (bucket provisioned)
	docker compose up -d --wait minio-test
	docker compose run --rm minio-test-init

observability-up: ## Start Prometheus (:9090) + Grafana (:3001) with provisioned dashboards
	docker compose up -d --wait prometheus grafana

observability-down: ## Stop Prometheus + Grafana (configs and dashboards stay in version control)
	docker compose stop prometheus grafana

db-migrate: ## Apply Alembic migrations to the dev database
	cd $(API_DIR) && uv run alembic upgrade head

db-seed: ## Seed the dev database — idempotent, safe to re-run
	@for f in database/postgres/seeds/*.sql; do \
		echo "seeding $$f"; \
		docker compose exec -T postgres psql -U $${POSTGRES_USER:-workpulse} -d $${POSTGRES_DB:-workpulse} -v ON_ERROR_STOP=1 < $$f; \
	done

db-down: ## Stop docker compose services (data volume is kept)
	docker compose down

dev: db-up redis-up kafka-up clickhouse-up minio-up db-migrate db-seed ## Run everything for browser testing (infra + migrate + seed + API :8000 + web :3000 + workers)
	bash scripts/dev.sh

dev-api: ## Run the FastAPI dev server on :8000
	cd $(API_DIR) && uv run uvicorn app.main:app --reload --port 8000

dev-web: ## Run the Next.js dev server on :3000
	cd $(WEB_DIR) && pnpm run dev

dev-relay: ## Run the outbox relay (publishes outbox rows to Kafka, ADR 0012)
	cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.relay.main

dev-realtime: ## Run the realtime consumer (attendance events → live wall, ADR 0013)
	cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.realtime.main

dev-notifications: ## Run the notification consumer (events → email/Slack deliveries, ADR 0015)
	cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.notifications.main

dev-payroll: ## Run the payroll calculation worker (queued runs → calculated, T8)
	cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.payroll.main

dev-reports: ## Run the reports projection worker (events → ClickHouse, ADR 0019)
	cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.reports.main

check: check-api check-web check-e2e ## Run every gate — run before claiming done

check-api: db-test-up redis-test-up kafka-test-up clickhouse-test-up minio-test-up ## API gate: ruff + pyright + pytest (ADR 0006)
	cd $(API_DIR) && uv run ruff check . ../../workers
	cd $(API_DIR) && uv run ruff format --check . ../../workers
	cd $(API_DIR) && uv run pyright . ../../workers
	cd $(API_DIR) && uv run pytest

check-web: ## Web gate: typecheck + ESLint + Vitest
	cd $(WEB_DIR) && pnpm run typecheck && pnpm run lint && pnpm run test

check-e2e: db-up redis-up kafka-test-up clickhouse-test-up minio-up db-migrate db-seed ## E2E gate: Playwright across the full event hop (kafka-test + ClickHouse + relay + realtime + payroll + reports)
	bash tests/e2e/full-hop.sh

gen-api: ## Regenerate OpenAPI contract + typed API client
	cd $(API_DIR) && uv run python scripts/export_openapi.py
	cd $(WEB_DIR) && pnpm run gen:api

format-web: ## Format the web app with Prettier
	cd $(WEB_DIR) && pnpm run format
