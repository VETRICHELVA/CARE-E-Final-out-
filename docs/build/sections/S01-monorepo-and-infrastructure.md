# S01 — Monorepo and infrastructure

**Milestone:** M0 · **Depends on:** none · **Workstream:** Backend lead · **Can run alongside:** nothing (everything builds on this)

## Read first
- `CLAUDE.md` — Stack, Commands, Conventions
- `docs/specs/api-and-events.md` — Conventions (how the API client is generated)

## Goal
A fresh clone runs `make up` and gets healthy Postgres, Redis and Mosquitto. Lint and tests run (on empty projects) locally and in CI.

## Build
- Folder layout from `CLAUDE.md`, each package or service with a one-paragraph README.
- **JavaScript:** pnpm workspaces (`apps/*`, `packages/*`), a shared `packages/tsconfig` (strict mode), ESLint and Prettier configs.
- **Python:** one `pyproject.toml` per service managed with uv; ruff, mypy and pytest configured; Python 3.12.
- `infra/docker-compose.yml`:
  - `postgres:16` and `redis:7`, with healthchecks and named volumes.
  - `eclipse-mosquitto:2` with a dev config (`infra/mosquitto/mosquitto.conf`, anonymous allowed on localhost only).
  - An `osrm` service under the compose profile `routing` (data is prepared in S11).
- `Makefile` with every command in `CLAUDE.md`. Targets for later sections print "Not available until Sxx" and exit 0.
- `.env.example` at the root and per service; `.gitignore`; `.editorconfig`; pre-commit hooks (ruff, ruff-format, prettier).
- GitHub Actions `ci.yml`, with three jobs:
  - `python`: lint and tests per service.
  - `web`: pnpm install, lint, typecheck, test.
  - `client-drift`: a placeholder for S03.

## Out of scope
Any application code, models or screens.

## Acceptance criteria
- [ ] `make up` starts Postgres, Redis and Mosquitto, and all report healthy; `make down` stops them.
- [ ] `make lint` and `make test` pass on the skeleton.
- [ ] The CI workflow is valid; checked with `act` if available, or by careful review.
- [ ] The Commands list in `CLAUDE.md` matches the Makefile exactly.

## Verify
```
make up && docker compose -f infra/docker-compose.yml ps
make lint && make test
make down
```
