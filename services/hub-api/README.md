# hub-api

The CARE-E hub: a FastAPI service (Python 3.12, SQLAlchemy 2 async, Alembic, Pydantic v2, arq) that owns every business rule, permission and state transition — matching, holds, recommendations, approvals, shipments, reconciliation and the append-only audit log. Its OpenAPI spec is the source for `packages/api-client`. Set up with `uv sync`; lint and test from the repo root with `make lint` and `make test-hub`. Application code arrives in S02.
