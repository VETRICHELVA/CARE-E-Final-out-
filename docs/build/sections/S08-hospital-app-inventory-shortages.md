# S08 — Hospital app: inventory, shortages and incoming requests

**Milestone:** M1 · **Depends on:** S03, S06, S07 · **Workstream:** Frontend · **Can run alongside:** S09

## Read first
- `docs/specs/apps-ai-iot.md` — Shared rules; hospital-web rows marked S08
- `docs/specs/business-rules.md` — §3 (gate names and reasons, for display), §8 (states, for status chips)
- `docs/specs/demo-scenarios.md` — Scenario 1 steps 1–3

## Goal
Hospital users can manage inventory, report a shortage, see exactly which sources qualify and why others don't, and answer incoming requests from other hospitals, with everything updating live.

## Build
- **Dashboard** (S08 parts): open shortages by status, incoming requests awaiting response with countdowns.
- **Inventory:**
  - The batches table with all quantity columns; **transferable** is visually emphasized and read-only.
  - Edit dialog, Verify action, CSV import with a per-row error report.
- **Shortages:**
  - List with status chips and a "New shortage" form, validated with zod.
  - After save, show the hub's shortfall. Never compute it in the browser.
- **Shortage detail:**
  - Status timeline.
  - Latest match run: ranked eligible candidates (source, transferable or offered qty, ETA, landed cost); rejected candidates grouped with their reason text.
  - Source requests with state.
  - A "Re-run match" button.
- **Incoming requests:** product, qty, requesting hospital, a deadline countdown; Accept, and Decline with an optional reason box.
- Live updates through `useEventStream()`. Every action button is gated by `can()` and the resource state.
- **Playwright test:** create a Scenario 1 shortage as Hospital A and see B eligible and C, D, E rejected with reasons. Then, in a second browser context, decline as Hospital B and watch A's detail page re-run.

## Out of scope
The recommendation decision panel, deliveries and receiving (S12); chat (S13, S17); forecasts (S18).

## Acceptance criteria
- [ ] Scenario 1 steps 1–3 can be done entirely in the UI with two browser contexts.
- [ ] Rejected candidates show the hub's exact reason text.
- [ ] A user without `shortage.create` sees no "New shortage" button; a store manager sees Accept and Decline.
- [ ] Loading, empty and error states exist on every list.
- [ ] The Playwright test passes.

## Verify
```
make test-web && pnpm --filter hospital-web typecheck && make e2e
```
