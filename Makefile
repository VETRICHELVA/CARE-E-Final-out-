# Every target here is listed under "Commands" in CLAUDE.md; keep them in sync.
-include .env
export

SERVICES := hub-api ai-service iot-ingest
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: install up down hub ingest migrate migration client seed lint test test-hub test-web e2e

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

# MQTT telemetry -> hub every 2 s. Its INGEST_TOKEN must match the hub's.
ingest:
	cd services/iot-ingest && uv run python -m app

migrate:
	cd services/hub-api && uv run alembic upgrade head

migration:
	$(if $(m),,$(error usage: make migration m="message"))
	cd services/hub-api && uv run alembic revision --autogenerate -m "$(m)"

# Imports the app to export its OpenAPI (no running server needed), then generates the TS types.
client:
	cd services/hub-api && uv run python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > ../../packages/api-client/openapi.json
	pnpm --filter @care-e/api-client generate

seed:
	cd services/hub-api && uv run python -m app.seed

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
e2e:
	pnpm --filter e2e exec playwright test
