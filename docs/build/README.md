# CARE-E build plan for Claude Code

The project is split into **20 sections**. Each one is sized for a single Claude Code session, has a clear scope, and ends with tests you can run. Sections map onto the five milestones of the 12-week execution plan.

## One-time setup
1. Create an empty project folder and run `git init`.
2. Copy everything from this kit into the repo root: `CLAUDE.md`, `.claude/`, `docs/`.
3. Commit: `git add -A && git commit -m "S00: project kit"`.
4. Start Claude Code in the repo root with `claude`.

## The loop for every section
1. **Start fresh.** Run `/clear` (or open a new session) so the previous section's context doesn't leak in.
2. **Build.** Run `/build-section S04`. Claude reads the brief and only the specs it needs, then shows a plan and **waits for your approval**. Read the plan; push back if it goes beyond the brief.
3. **Review.** When it finishes, run `/check-section S04` for an independent read-only check against the acceptance criteria. Fix anything marked FAIL in the same session.
4. **Commit.** Use the message Claude proposes (starting `S04:`). One section, one commit or one pull request.
5. **Log.** Check that `docs/build/PROGRESS.md` is updated, and look over the Follow-ups list.

If a section feels too big for one session (most likely S11 and S12), ask Claude to do the hub half first, commit, `/clear`, then run `/build-section` again for the UI half.

## Sections
| ID | Section | Milestone | Depends on | Workstream |
|---|---|---|---|---|
| S01 | Monorepo and infrastructure | M0 | — | Backend |
| S02 | Hub foundation: auth, orgs, roles, audit | M0 | S01 | Backend |
| S03 | Frontend foundation and API client | M0 | S02 | Frontend |
| S04 | Catalog and inventory | M1 | S02 | Backend |
| S05 | Shortages and the matching engine | M1 | S04 | Backend |
| S06 | Source requests, holds and timers | M1 | S05 | Backend |
| S07 | Events, webhooks and live updates | M1 | S06, S03 | Backend |
| S08 | Hospital app: inventory, shortages, requests | M1 | S03, S06, S07 | Frontend |
| S09 | Recommendations, approvals, purchase orders | M2 | S06, S07 | Backend |
| S10 | Supplier app | M2 | S03, S09 | Frontend |
| S11 | Shipments, routing and the delivery app | M2 | S09, S03, S07 | Logistics + Frontend |
| S12 | Receiving, reconciliation, decision screens | M2 | S08, S09, S11 | Backend + Frontend |
| S13 | AI service and copilot | M2 | S12 | AI |
| S14 | IoT telemetry pipeline | M3 | S02 (S11 to link shipments) | IoT |
| S15 | Cold-chain rules and alerts | M3 | S14, S12 | IoT + Frontend |
| S16 | Route optimization | M3 | S11 | Logistics |
| S17 | Chat ordering | M3 | S13, S08 | AI + Frontend |
| S18 | Forecasting and expiry surplus | M3 | S12, S07 | AI + Frontend |
| S19 | Critical mode, reliability and credits | M3 | S09, S12 | Backend |
| S20 | Demo scenarios, E2E tests and hardening | M4 | all | Everyone |

## Critical path
S01 → S02 → S04 → S05 → S06 → S07 → S09 → S11 → S12 → S20. A delay on any of these delays the demo. Everything else hangs off this chain.

## Working in parallel (team of 4)
Each person runs Claude Code in their own **git worktree** (`git worktree add ../care-e-s08 -b s08`) so sessions never edit the same checkout. Merge in dependency order.

| Weeks | Backend | Frontend | AI/ML | IoT + logistics |
|---|---|---|---|---|
| 1 (M0) | S01, S02 | S03 (after S02) | Write eval sets for S13 and S17 | Order hardware; S14 firmware and simulator |
| 2–4 (M1) | S04, S05, S06, S07 | S08 | S18 synthetic data generator (seed script part) | S14 firmware; prepare OSRM data |
| 5–7 (M2) | S09, then S12 hub half | S10, S11 UI, S12 UI | S13 | S11 hub half (routing) |
| 8–10 (M3) | S19 | S15 UI, S17 UI, S18 UI | S17, S18 | S14 hub part, S15, S16 |
| 11–12 (M4) | S20 | S20 | S20 | S20 |

Working alone? Follow the table top to bottom, left to right, and expect roughly 2–3 sections a week.

## Rules that keep sessions on track
- **Specs are the source of truth.** If code and spec disagree, Claude must ask, and update the spec in the same change once you agree.
- **Stay in scope.** Out-of-scope work goes into Follow-ups in PROGRESS.md, not into the current section.
- **Use the spec-guardian subagent.** Ask Claude to "run the spec-guardian on this diff" before committing changes to matching, holds, state machines, reconciliation, audit, auth or AI tools.
- **Commands stay current.** Any new `make` target goes into `CLAUDE.md` in the same section.
- **When stuck:** stop the session, write what's blocking under Follow-ups, and start a fresh session with a narrower request.
