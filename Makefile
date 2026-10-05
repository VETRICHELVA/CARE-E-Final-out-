# Every target here is listed under "Commands" in CLAUDE.md; keep them in sync.
-include .env
export

SERVICES := hub-api ai-service iot-ingest
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: install up down hub migrate migration client seed lint test test-hub test-web

install:
	for s in $(SERVICES); do (cd services/$$s && uv sync) || exit 1; done
	pnpm install
	uvx pre-commit install

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

hub:
	cd services/hub-api && uv run uvicorn --factory app.main:create_app --reload --port 8000

migrate:
	cd services/hub-api && uv run alembic upgrade head

migration:
	$(if $(m),,$(error usage: make migration m="message"))
	cd services/hub-api && uv run alembic revision --autogenerate -m "$(m)"

client:
	@echo "Not available until S03"

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
