# Every target here is listed under "Commands" in CLAUDE.md; keep them in sync.
-include .env
# Every target here is local development: the hub, worker, seed, demo-reset, migrate, ingest,
# ai, client, tests and e2e run with APP_ENV=dev unless APP_ENV is set (shell or root .env).
# Without it the services count as production and refuse the committed dev secrets.
APP_ENV ?= dev
export

SERVICES := hub-api ai-service iot-ingest
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: install up down hub ai worker ingest migrate migration client seed demo-reset lint test test-hub test-web e2e eval-ai

install:
	for s in $(SERVICES); do (cd services/$$s && uv sync) || exit 1; done
	pnpm install
	pnpm --filter e2e exec playwright install chromium
	uvx pre-commit install

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

hub:
	cd services/hub-api && uv run uvicorn --factory app.main:create_app --reload --port 8000

# The AI service (copilot) on :8100; reads the hub only through GET /ai/read/*. Without
# AI_API_KEY (or ANTHROPIC_API_KEY) it runs and answers "AI is not configured".
ai:
	cd services/ai-service && uv run uvicorn --factory app.main:create_app --reload --port 8100

# MQTT telemetry -> hub every 2 s. Its INGEST_TOKEN must match the hub's.
ingest:
	cd services/iot-ingest && uv run python -m app

# arq worker: deadline timers, the event publisher (SSE live updates) and webhook deliveries.
worker:
	cd services/hub-api && uv run arq app.worker.WorkerSettings

migrate:
	cd services/hub-api && uv run alembic upgrade head

migration:
	$(if $(m),,$(error usage: make migration m="message"))
	cd services/hub-api && uv run alembic revision --autogenerate -m "$(m)"

# Imports the app to export its OpenAPI (no running server needed), then generates the TS types.
client:
	cd services/hub-api && uv run python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > ../../packages/api-client/openapi.json
	pnpm --filter @care-e/api-client generate

# The demo seed (docs/specs/demo-scenarios.md): adds what is missing, times relative to now; it
# never changes existing stock or offers (no audit row). `make demo-reset` starts from scratch.
seed:
	cd services/hub-api && uv run python -m app.seed

# Drop the local dev database `care`, migrate and seed it. Refuses any other database
# (APP_ENV must be dev, set here or explicitly; DATABASE_URL a loopback host; database `care`).
demo-reset:
	cd services/hub-api && uv run python -m app.demo_reset

lint:
	for s in $(SERVICES); do (cd services/$$s && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests) || exit 1; done
	pnpm lint
	pnpm typecheck

test: test-web
	for s in $(SERVICES); do (cd services/$$s && uv run pytest) || exit 1; done

test-hub:
	cd services/hub-api && uv run pytest

test-web:
	pnpm test

# Starts the hub and the three apps unless already running; needs `make up` and a freshly seeded
# database: `make demo-reset` (or `make migrate seed` on an empty one). The seeds only add what
# is missing, so stock, "verified" times and offers age between runs; reset before a later run.
# Cancels Hospital A's open Surgical Kit A shortages first. A hub Playwright starts allows
# E2E_LOGIN_RATE_LIMIT logins per minute per IP (a dev-only override; a hub you started yourself
# keeps its own LOGIN_RATE_LIMIT, default 5).
E2E_LOGIN_RATE_LIMIT ?= 100
e2e:
	cd services/hub-api && uv run python ../../e2e/seed/scenario1.py
	LOGIN_RATE_LIMIT=$(E2E_LOGIN_RATE_LIMIT) pnpm --filter e2e exec playwright test

# The AI evals (services/ai-service/evals) against the real model: the copilot's 15 Scenario 1
# questions, then chat ordering's 20 phrasings (S17; pass bar 18). Both run even if the first
# fails. Skip (exit 0) without a key; otherwise need `make up migrate seed` and the hub running.
eval-ai:
	cd services/ai-service && status=0; \
	uv run python -m app.evals || status=$$?; \
	uv run python -m app.chat_evals || status=$$?; \
	exit $$status
