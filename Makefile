.DEFAULT_GOAL := help

API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: help install dev db-up db-down db-test-up redis-up redis-test-up kafka-up kafka-test-up db-migrate db-seed dev-api dev-web dev-relay dev-realtime dev-notifications check check-api check-web check-e2e gen-api format-web

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

db-migrate: ## Apply Alembic migrations to the dev database
	cd $(API_DIR) && uv run alembic upgrade head

db-seed: ## Seed the dev database — idempotent, safe to re-run
	@for f in database/postgres/seeds/*.sql; do \
		echo "seeding $$f"; \
		docker compose exec -T postgres psql -U $${POSTGRES_USER:-workpulse} -d $${POSTGRES_DB:-workpulse} -v ON_ERROR_STOP=1 < $$f; \
	done

db-down: ## Stop docker compose services (data volume is kept)
	docker compose down

dev: db-up redis-up kafka-up db-migrate db-seed ## Run everything for browser testing (infra + migrate + seed + API :8000 + web :3000 + workers)
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

check: check-api check-web check-e2e ## Run every gate — run before claiming done

check-api: db-test-up redis-test-up kafka-test-up ## API gate: ruff + pyright + pytest (ADR 0006)
	cd $(API_DIR) && uv run ruff check . ../../workers
	cd $(API_DIR) && uv run ruff format --check . ../../workers
	cd $(API_DIR) && uv run pyright . ../../workers
	cd $(API_DIR) && uv run pytest

check-web: ## Web gate: typecheck + ESLint + Vitest
	cd $(WEB_DIR) && pnpm run typecheck && pnpm run lint && pnpm run test

check-e2e: db-up redis-up kafka-test-up db-migrate db-seed ## E2E gate: Playwright across the full event hop (kafka-test + relay + realtime)
	bash tests/e2e/full-hop.sh

gen-api: ## Regenerate OpenAPI contract + typed API client
	cd $(API_DIR) && uv run python scripts/export_openapi.py
	cd $(WEB_DIR) && pnpm run gen:api

format-web: ## Format the web app with Prettier
	cd $(WEB_DIR) && pnpm run format
