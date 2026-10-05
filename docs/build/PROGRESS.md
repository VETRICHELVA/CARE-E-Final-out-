# CARE-E build progress

Updated by `/build-section` at the end of each section. Status: `todo`, `in progress`, `done`, or `cut`.

| ID | Section | Status | Done on | Notes |
|---|---|---|---|---|
| S01 | Monorepo and infrastructure | todo | | |
| S02 | Hub foundation: auth, orgs, roles, audit | todo | | |
| S03 | Frontend foundation and API client | todo | | |
| S04 | Catalog and inventory | todo | | |
| S05 | Shortages and the matching engine | todo | | |
| S06 | Source requests, holds and timers | todo | | |
| S07 | Events, webhooks and live updates | todo | | |
| S08 | Hospital app: inventory, shortages, requests | todo | | |
| S09 | Recommendations, approvals, purchase orders | todo | | |
| S10 | Supplier app | todo | | |
| S11 | Shipments, routing and the delivery app | todo | | |
| S12 | Receiving, reconciliation, decision screens | todo | | |
| S13 | AI service and copilot | todo | | |
| S14 | IoT telemetry pipeline | todo | | |
| S15 | Cold-chain rules and alerts | todo | | |
| S16 | Route optimization | todo | | |
| S17 | Chat ordering | todo | | |
| S18 | Forecasting and expiry surplus | todo | | |
| S19 | Critical mode, reliability and credits | todo | | |
| S20 | Demo scenarios, E2E tests and hardening | todo | | |

## Milestone gates
- **M0** (S01–S03): every app logs in against the hub; CI green.
- **M1** (S04–S08): a shortage finds eligible sources, and one accepts with a hold, in the hospital app.
- **M2** (S09–S13): the full loop from shortage to reconciliation across all three apps, with audit and copilot.
- **M3** (S14–S19): chat, forecasts, surplus, optimization, cold chain, Critical mode.
- **M4** (S20): three demo scenarios pass three times in a row.

## Follow-ups
<!-- Things noticed during a section that belong to another section or to later. Format: - [Sxx → Syy] description -->

## Known gaps
<!-- Things knowingly left incomplete, with the reason. -->

## Hardening checklist (S20)
<!-- Record the outcome of each check from S20. -->
