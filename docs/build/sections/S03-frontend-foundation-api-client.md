# S03 — Frontend foundation and API client

**Milestone:** M0 · **Depends on:** S02 · **Workstream:** Frontend · **Can run alongside:** S04, S05 (backend)

## Read first
- `docs/specs/apps-ai-iot.md` — Shared rules for all three apps
- `docs/specs/api-and-events.md` — Conventions; Realtime in the apps

## Goal
Three app shells that log in against the hub, share one UI kit and one generated, typed API client, with CI catching a stale client.

## Build
- **`packages/api-client`:**
  - `make client` exports the hub's OpenAPI to `packages/api-client/openapi.json` (a Python script that imports the app; no running server needed), then generates types with openapi-typescript.
  - A typed `openapi-fetch` client that injects the access token and refreshes once on 401.
  - TanStack Query helpers.
- **CI:** the `client-drift` job runs `make client` and fails if git shows changes.
- **`packages/ui`:**
  - Tailwind preset and design tokens, including status colors.
  - shadcn/ui components: Button, Card, Table, Badge, Dialog, Form, Input, Select, Textarea, Toast, Tabs.
  - `StatusChip`, mapping every state in `business-rules.md` §8 to a label and color.
  - Formatters: money (₹ with Indian digit grouping, from paise), quantity with unit, date/time in local zone.
  - EmptyState, ErrorState and Loading components.
- **Shared auth:** login page, Zustand auth store, `ProtectedRoute`, `useMe()`, and a `can(capability)` helper.
- **Three Vite + React Router apps** (ports 5173, 5174, 5175):
  - A header showing user, org and org type, plus nav from `apps-ai-iot.md` with placeholder pages.
  - Each app accepts only its org type (hospital-web also lets PLATFORM users in, for the admin page in S20). Anyone else sees "This app is for hospital users" or the equivalent.
- **Tests:** Vitest + Testing Library setup in each app; a new `e2e/` workspace package (name `e2e`) holding Playwright, with a smoke test that logs into each app. Add `make e2e` and list it in `CLAUDE.md`.

## Out of scope
Real screens (S08, S10, S11), and live events (S07).

## Acceptance criteria
- [ ] `make client` regenerates the client; CI fails when it is stale.
- [ ] Each app logs in with seeded users and shows the org name; a user of the wrong org type is refused clearly.
- [ ] `can()` hides an action for a user without the capability (unit test).
- [ ] Formatter unit tests: 12345600 paise → "₹1,23,456.00".

## Verify
```
make client && git diff --exit-code packages/api-client
pnpm -r typecheck && pnpm -r test
pnpm --filter e2e exec playwright test smoke
```
