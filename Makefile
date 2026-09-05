# Nihongo Tutor developer entry points. Run `make help`.
SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND := backend
MINIAPP := miniapp
UV := cd $(BACKEND) && uv run
COMPOSE_DEV := docker compose -f infra/docker-compose.dev.yml
COMPOSE := docker compose -f infra/docker-compose.yml --env-file infra/.env

.PHONY: help setup dev api worker miniapp db-up db-down test test-unit lint fmt typecheck check \
        migrate revision downgrade openapi gen-api seed gen-content compose-up compose-down compose-logs deploy backup

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------
setup: ## Install backend (uv) and miniapp (npm) dependencies
	cd $(BACKEND) && uv sync
	cd $(MINIAPP) && npm install --no-audit --no-fund

db-up: ## Start local Postgres + Redis in Docker
	$(COMPOSE_DEV) up -d --wait

db-down: ## Stop local Postgres + Redis (keeps data)
	$(COMPOSE_DEV) down

# --- run ---------------------------------------------------------------------
dev: ## Run API (reload) and Vite dev server together (Ctrl-C stops both)
	@trap 'kill 0' INT TERM; \
	$(MAKE) --no-print-directory api & \
	$(MAKE) --no-print-directory miniapp & \
	wait

api: ## Run FastAPI with autoreload on :8000
	$(UV) uvicorn app.main:create_app --factory --reload --port 8000

worker: ## Run the arq worker
	$(UV) arq app.workers.worker.WorkerSettings

miniapp: ## Run the Vite dev server on :5173
	cd $(MINIAPP) && npm run dev

# --- quality -----------------------------------------------------------------
test: ## Run the full backend test suite (needs Postgres: TEST_DATABASE_URL or Docker)
	$(UV) pytest -q

test-unit: ## Run only tests that need no database
	$(UV) pytest -q -m "not integration"

lint: ## ruff + black --check (backend), tsc (miniapp)
	$(UV) ruff check .
	$(UV) black --check .
	cd $(MINIAPP) && npm run typecheck

fmt: ## Auto-format backend
	$(UV) black .
	$(UV) ruff check --fix .

typecheck: ## mypy strict on backend/app
	$(UV) mypy

check: lint typecheck test ## Everything CI runs

# --- database ----------------------------------------------------------------
migrate: ## alembic upgrade head
	$(UV) alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add streaks"
	$(UV) alembic revision --autogenerate -m "$(m)"

downgrade: ## alembic downgrade -1
	$(UV) alembic downgrade -1

# --- codegen -----------------------------------------------------------------
openapi: ## Export backend/openapi.json from the FastAPI app
	$(UV) python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))" > $(BACKEND)/openapi.json

gen-api: openapi ## Regenerate miniapp/src/api/schema.d.ts from the OpenAPI spec
	cd $(MINIAPP) && npm run gen:api

# --- content (Phase 1+) ------------------------------------------------------
seed: ## Import seed content (kana etc.)
	$(UV) nihongo-content check

gen-content: ## Run AI content generation batches
	@echo "content pipeline arrives with Phase 2 (see docs/PLAN.md)"; $(UV) nihongo-content check

# --- production --------------------------------------------------------------
compose-up: ## Build and start the production stack (needs infra/.env)
	$(COMPOSE) up -d --build

compose-down: ## Stop the production stack
	$(COMPOSE) down

compose-logs: ## Tail production logs
	$(COMPOSE) logs -f --tail=200

deploy: ## Pull, rebuild, restart, smoke-test on the server
	infra/scripts/deploy.sh

backup: ## pg_dump + audio tarball into ./backups
	infra/scripts/backup.sh
