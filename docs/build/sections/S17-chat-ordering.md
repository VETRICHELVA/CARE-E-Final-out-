# S17 — Chat ordering

**Milestone:** M3 · **Depends on:** S13, S08 · **Workstream:** AI/ML + Frontend · **Can run alongside:** S15, S16, S18

## Read first
- `CLAUDE.md` — Non-negotiable rules 2 and 3
- `docs/specs/apps-ai-iot.md` — Chat ordering
- `docs/specs/business-rules.md` — §1 (shortfall), §8 Shortage table (DRAFT → OPEN)

## Goal
A hospital user types a request in plain language and gets a pre-filled shortage card to check and confirm. The AI never creates anything itself, and never guesses between similar products.

## Build
- **Hub:** `GET /api/v1/ai/read/products/search?q=` (service token): fuzzy match on name, code and a synonyms list (`scripts/seed/synonyms.yaml`, e.g. "SK-A", "surgical kit A", "kit A") with a score.
- **ai-service `POST /chat/draft {message, user_tz, now}`** returns `{draft, missing_fields, product_candidates, question}`:
  - Resolves the product. If more than one candidate scores within 10% of the best, return `product_candidates` and a `question` instead of choosing.
  - Resolves relative dates in `user_tz` and echoes them back as absolute times.
  - Defaults when unstated: priority ROUTINE, `min_shelf_life_days` from the product default, `qty_local_usable` 0. These are listed in `missing_fields` so the card highlights them.
- **hospital-web chat panel, "Order" mode:**
  - A confirmation card with every field editable and the required-by date shown in full.
  - Buttons: "Create shortage" (`POST /shortages` as the user, `source=CHAT`, status OPEN), "Save as draft" (status DRAFT), and "Cancel".
  - Product choices appear as buttons when ambiguous.
- **Evaluation:** `services/ai-service/evals/chat_orders.jsonl`, 20 phrasings including Indian English and shorthand, e.g. "need 850 SK-A by fri for ICU", "urgent: 200 rapid kits tomorrow morning". `make eval-ai` reports the score.

## Out of scope
Voice input; ordering more than one product in a message (the AI should ask the user to split it).

## Acceptance criteria
- [ ] At least 18 of 20 eval phrasings produce a correct draft or a correct clarifying question.
- [ ] An ambiguous product always produces a question; it is never silently picked (test).
- [ ] The AI service has no route to create shortages; its token is refused on `POST /shortages` (test).
- [ ] "By Friday" resolves correctly for Asia/Kolkata and UTC users (unit tests with a fixed `now`).
- [ ] A shortage created from chat shows `source=CHAT` in its audit row, attributed to the user.

## Verify
```
(cd services/ai-service && uv run pytest) && make test-hub && make test-web
make eval-ai   # needs AI_API_KEY
```
