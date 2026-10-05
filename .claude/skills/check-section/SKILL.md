---
name: check-section
description: Independently re-check a finished CARE-E section against its acceptance criteria and the business-rule specs. Use when the user types /check-section followed by a section ID.
argument-hint: "[section-id e.g. S05]"
disable-model-invocation: true
context: fork
---

Review section $ARGUMENTS of CARE-E as an independent reviewer who did not write the code.

1. Read `CLAUDE.md`, then the file in `docs/build/sections/` whose name starts with `$ARGUMENTS-`.
2. Read the spec files that brief lists under "Read first".
3. For each acceptance criterion, find the evidence in code and tests and run the "Verify" commands. Mark each one PASS, FAIL or UNCLEAR with a file and line reference.
4. Check the non-negotiable rules in `CLAUDE.md` for the code this section touched:
   - business rules enforced only in the hub
   - org scoping on every query, with a 403 test
   - 409 on invalid state transitions
   - an audit row for every state change, with the correct `reason_source`
   - AI code using only read-only tools
5. Do not edit any files. Report a table of criteria with status and evidence, then a short list of fixes in priority order.
