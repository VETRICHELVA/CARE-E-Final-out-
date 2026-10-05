---
name: build-section
description: Build one CARE-E development section (S01–S20) from its brief in docs/build/sections. Use when the user types /build-section followed by a section ID.
argument-hint: "[section-id e.g. S05]"
disable-model-invocation: true
---

# Build section $ARGUMENTS

## Current progress
!`cat docs/build/PROGRESS.md`

## Steps

1. **Load the brief.** Read the file in `docs/build/sections/` whose name starts with `$ARGUMENTS-`. If none matches, list that folder and stop.
2. **Check dependencies.** Every section under "Depends on" must be marked done in PROGRESS.md above. If one isn't, say which and stop unless the user says to continue.
3. **Read only the spec sections the brief names** under "Read first". Look at the existing code in the areas you will touch before writing new code.
4. **Plan first.** Present a short plan: files to create or change, migrations, endpoints, screens, tests, and any question the brief leaves open. Wait for the user to approve before editing anything.
5. **Build in small steps.** After each logical step, run the relevant tests and lint. Keep to the brief's "Build" list; anything in "Out of scope" or unrelated goes into PROGRESS.md under Follow-ups.
6. **Contract changes.** If you changed hub endpoints or schemas, run `make client` and fix any frontend type errors it causes.
7. **Verify.** Run every command under "Verify" in the brief. Tick an acceptance criterion only when you have evidence: a passing test, command output, or a screen you checked.
8. **Finish.**
   - Update `docs/build/PROGRESS.md`: mark $ARGUMENTS done with today's date; add follow-ups and known gaps.
   - If a rule or contract changed, update the matching file in `docs/specs/`.
   - If you added or changed a command, update the Commands list in `CLAUDE.md`.
   - Propose a commit message starting `$ARGUMENTS:` and summarize what was built, how to run it, and what's left.
