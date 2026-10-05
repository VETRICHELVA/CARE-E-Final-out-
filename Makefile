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

hub migrate migration:
	@echo "Not available until S02"

client:
	@echo "Not available until S03"

seed:
	@echo "Not available until S20"

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
