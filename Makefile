.DEFAULT_GOAL := help

API_DIR := apps/api
WEB_DIR := apps/web

# Deployment images are tagged with the commit SHA (ADR 0026); `latest` is a
# local convenience tag only.
IMAGE_TAG ?= sha-$(shell git rev-parse --short HEAD)
API_IMAGE ?= workpulse-api:$(IMAGE_TAG)
WEB_IMAGE ?= workpulse-web:$(IMAGE_TAG)

.PHONY: help install dev db-up db-down db-test-up redis-up redis-test-up kafka-up kafka-test-up clickhouse-up clickhouse-down clickhouse-test-up minio-up minio-test-up observability-up observability-down cdc-up cdc-down cdc-test-up cdc-test-down db-migrate db-seed dev-api dev-web dev-relay dev-realtime dev-notifications dev-payroll dev-cdc-sink check check-api check-web check-e2e check-load images gen-api format-web

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

cdc-up: db-up db-migrate clickhouse-up ## Start PostgreSQL/ClickHouse/Kafka/Connect and register the CDC projection
	docker compose up -d --wait kafka kafka-connect
	CDC_PROJECTION_DATABASE=workpulse bash ops/cdc/register.sh

cdc-down: ## Stop Kafka Connect (connector and offset state stay in Kafka)
	docker compose stop kafka-connect

cdc-test-up: db-up db-migrate clickhouse-test-up ## Start the throwaway test Kafka Connect (:8084) and register against kafka-test
	docker compose up -d --wait kafka-test kafka-connect-test
	CONNECT_URL=http://localhost:8084 \
	CDC_SLOT_NAME=workpulse_cdc_test \
	CDC_CLICKHOUSE_SERVICE=clickhouse-test \
	CDC_PROJECTION_DATABASE=workpulse_test \
	bash ops/cdc/register.sh

cdc-test-down: ## Stop the throwaway test Kafka Connect (:8084)
	docker compose stop kafka-connect-test

db-migrate: ## Apply Alembic migrations to the dev database
	cd $(API_DIR) && uv run alembic upgrade head

db-seed: ## Seed the dev database — idempotent, safe to re-run
	@for f in database/postgres/seeds/*.sql; do \
		echo "seeding $$f"; \
		docker compose exec -T postgres psql -U $${POSTGRES_USER:-workpulse} -d $${POSTGRES_DB:-workpulse} -v ON_ERROR_STOP=1 < $$f; \
	done

db-down: ## Stop docker compose services (data volume is kept)
	docker compose down

dev: db-up redis-up kafka-up clickhouse-up minio-up db-migrate db-seed cdc-up ## Run everything for browser testing (infra + migrate + seed + CDC + API :8000 + web :3000 + workers)
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

dev-cdc-sink: ## Run the CDC projection sink (Debezium topics → ClickHouse, ADR 0024)
	cd $(API_DIR) && PYTHONPATH="../..:." uv run python -m workers.cdc_sink.main

check: check-api check-web check-e2e check-load ## Run every gate — run before claiming done

check-api: db-test-up redis-test-up kafka-test-up clickhouse-test-up minio-test-up ## API gate: ruff + pyright + pytest (ADR 0006)
	cd $(API_DIR) && uv run ruff check . ../../workers
	cd $(API_DIR) && uv run ruff format --check . ../../workers
	cd $(API_DIR) && uv run pyright . ../../workers
	cd $(API_DIR) && uv run pytest

check-web: ## Web gate: typecheck + ESLint + Vitest
	cd $(WEB_DIR) && pnpm run typecheck && pnpm run lint && pnpm run test

check-e2e: db-up redis-up kafka-test-up clickhouse-test-up minio-up db-migrate db-seed cdc-test-up ## E2E gate: Playwright across the full event hop (kafka-test + ClickHouse + Connect + relay + realtime + payroll + CDC)
	bash tests/e2e/full-hop.sh

check-load: db-up redis-up db-migrate db-seed ## Load gate: k6 smoke at 25 check-ins/s, p95 < 300ms, 0 errors (ADR 0025)
	bash tests/performance/run.sh

images: ## Build the API and web container images tagged sha-<commit> (deployment)
	docker build -f apps/api/Dockerfile -t $(API_IMAGE) -t workpulse-api:latest .
	docker build -f apps/web/Dockerfile -t $(WEB_IMAGE) -t workpulse-web:latest .

gen-api: ## Regenerate OpenAPI contract + typed API client
	cd $(API_DIR) && uv run python scripts/export_openapi.py
	cd $(WEB_DIR) && pnpm run gen:api

format-web: ## Format the web app with Prettier
	cd $(WEB_DIR) && pnpm run format
