# Every target here is listed under "Commands" in CLAUDE.md; keep them in sync.
-include .env
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

# The demo seed (docs/specs/demo-scenarios.md): idempotent, times relative to now.
seed:
	cd services/hub-api && uv run python -m app.seed

# Drop the local dev database `care`, migrate and seed it. Refuses any other database
# (APP_ENV must be dev, DATABASE_URL a loopback host and the database `care`).
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

# Starts the hub and the three apps unless already running; needs `make up migrate seed` first.
# Resets Scenario 1 first (the demo seed's numbers, Hospital A's open Surgical Kit A shortages
# cancelled). A hub Playwright starts allows E2E_LOGIN_RATE_LIMIT logins per minute per IP
# (a hub you started yourself keeps its own LOGIN_RATE_LIMIT, default 5).
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
