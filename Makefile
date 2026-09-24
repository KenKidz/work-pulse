.DEFAULT_GOAL := help

API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: help install db-up db-down dev-api dev-web check check-api check-web

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install API (uv) and web (npm) dependencies
	cd $(API_DIR) && uv sync
	cd $(WEB_DIR) && npm install

db-up: ## Start PostgreSQL (detached)
	docker compose up -d

db-down: ## Stop docker compose services (data volume is kept)
	docker compose down

dev-api: ## Run the FastAPI dev server on :8000
	cd $(API_DIR) && uv run uvicorn app.main:app --reload --port 8000

dev-web: ## Run the Next.js dev server on :3000
	cd $(WEB_DIR) && npm run dev

check: check-api check-web ## Run every gate — run before claiming done

check-api: ## API gate: pytest
	cd $(API_DIR) && uv run pytest

check-web: ## Web gate: TypeScript typecheck
	cd $(WEB_DIR) && npm run typecheck