# hub-api

The CARE-E hub: a FastAPI service (Python 3.12, SQLAlchemy 2 async, Alembic, Pydantic v2, arq) that owns every business rule, permission and state transition — matching, holds, recommendations, approvals, shipments, reconciliation and the append-only audit log. Its OpenAPI spec (`/api/v1/openapi.json`) is the source for `packages/api-client`.

From the repo root: `make up && make migrate && make seed`, then `make hub` (http://localhost:8000, docs at `/api/v1/docs`). Tests (`make test-hub`) need `make up`; they recreate a separate `care_test` database and use Redis DB 15. Settings come from env or `.env` (see `.env.example`).

Layout: `app/<module>/{models,schemas,service,router}.py` + `tests/`; shared test fixtures in `app/conftest.py`; pure domain rules in `app/domain/`; migrations in `migrations/`.
