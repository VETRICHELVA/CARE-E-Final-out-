# S02 — Hub foundation: auth, organizations, roles and audit

**Milestone:** M0 · **Depends on:** S01 · **Workstream:** Backend · **Can run alongside:** S14 firmware and simulator work

## Read first
- `docs/specs/domain-model.md` — Identity; Records (AuditLog only)
- `docs/specs/api-and-events.md` — Conventions; S02 rows of the endpoint table
- `docs/specs/business-rules.md` — §8 (intro line) and §10 Audit

## Goal
A running hub with login, organization-scoped permissions, a generic state-machine helper and an append-only audit log that every later section reuses.

## Build
- **App skeleton:**
  - FastAPI app factory, settings via pydantic-settings, async SQLAlchemy session, and an Alembic baseline.
  - An error handler producing `{code, message, details}`, request-ID middleware, structured JSON logging, and `GET /health`.
- **Models:** Organization, Facility, User, Role, UserRole. Capabilities are a fixed role → capability map in code (`app/auth/capabilities.py`).
- **Auth:**
  - Argon2 password hashing.
  - JWT access tokens (15 min) and rotating refresh tokens (7 days, stored hashed, revoked on logout).
  - Endpoints: `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`.
  - Login rate limit: 5 per minute per IP, using Redis.
- **Authorization helpers:**
  - `require(capability)` dependency.
  - `org_scoped(query, user)` helper.
  - A `PublicOrgView` schema (name, type, location) for cross-org reads.
- **Audit:**
  - `audit.record(session, actor, entity, entity_id, action, before, after, reason)` sets `reason_source` automatically: USER when a non-blank reason is given, else SYSTEM with "No reason was entered." System actors pass an explicit cause.
  - A Postgres trigger blocks UPDATE and DELETE on `audit_log`.
  - `GET /audit`.
- **State machine helper:** `transition(obj, to_state, allowed: dict[from, set[to]])` raises `InvalidTransition`, which maps to 409 `invalid_transition`.
- **Test fixtures:** two hospitals and one supplier, a user per role, and an authenticated client factory.
- A minimal dev seed: platform admin, Hospital A and Hospital B with users. The full seed comes in S20.

## Out of scope
Products, inventory, and any domain entity beyond identity and audit.

## Acceptance criteria
- [ ] Login, refresh, logout and me work; an expired or revoked token returns 401.
- [ ] A missing capability returns 403; a Hospital A user reading Hospital B's org gets only public fields.
- [ ] Audit helper tests cover both USER and SYSTEM reason paths; a raw SQL UPDATE on `audit_log` fails.
- [ ] State machine helper: an allowed transition succeeds, a disallowed one returns 409 through the API.
- [ ] OpenAPI is served at `/api/v1/openapi.json`.

## Verify
```
make up && make migrate && make test-hub && make lint
curl -s localhost:8000/health
```
