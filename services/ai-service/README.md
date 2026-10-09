# ai-service

The CARE-E AI service (FastAPI, LLM tool calling). S13 adds the copilot; S17 adds chat ordering.
It reads the hub through read-only tools, drafts but never commits, and states no figure a tool
didn't return (CLAUDE.md rule 2). Set up with `uv sync`; lint and test from the repo root with
`make lint` and `make test`; run it with `make ai` (port 8100).

## Configuration (`.env.example`)

| Variable           | Meaning                                                                      |
| ------------------ | ---------------------------------------------------------------------------- |
| `HUB_API_URL`      | The hub, e.g. `http://127.0.0.1:8000/api/v1`                                 |
| `AI_SERVICE_TOKEN` | Must equal the hub's `AI_SERVICE_TOKEN` (scope `ai.read`)                    |
| `AI_PROVIDER`      | `anthropic` (the only provider)                                              |
| `AI_MODEL`         | Default `claude-opus-5-5`                                                    |
| `AI_API_KEY`       | The provider key; `ANTHROPIC_API_KEY` is used when it is unset. Never commit |
| `AI_EFFORT`        | Thinking effort, default `medium`                                            |
| `AI_CHAT_EFFORT`   | Thinking effort for chat ordering's extraction (S17), default `medium`       |

Without a key the service still starts: `GET /status` says `{"configured": false}` and
`POST /copilot/ask` answers 503 `{"error": "ai_not_configured"}`. Nothing else in CARE-E
depends on it.

## API

- `POST /copilot/ask` `{question, context: {shortage_id?, recommendation_id?, shipment_id?}}`
  with the signed-in user's hub access token as `Authorization: Bearer` →
  `{answer, tool_trace: [{tool, input, ok, label, result, from_context}]}`. `label` is the
  "Based on:" chip. 401 when the hub refuses the user's token (refresh and retry), 502 when the
  hub or the model is unreachable.
- `POST /chat/draft` `{message, user_tz, now?}` (S17), same bearer → `{draft, missing_fields,
product_candidates, question, assumptions, tool_trace}`. `draft` holds the card's fields
  (`product_id`/`_code`/`_name`, `qty_required`, `qty_local_usable`, `required_by` as ISO 8601
  in `user_tz` plus `required_by_display` and the user's own words, `priority`,
  `min_shelf_life_days`, `notes`); it is null only when the message names several products or
  the model declined. `missing_fields` lists what the message did not give, defaults included
  (priority ROUTINE, the product's `min_shelf_life_days`, `qty_local_usable` 0).
  `product_candidates` is set, and `product_id` null, whenever another product scores within
  10% of the best. `now` (aware) anchors relative dates; default the server's clock. 422 for an
  unknown zone or a naive `now`.
- `GET /status` → `{configured, model}`; `GET /health`.

## How rule 2 is kept

- **Read-only.** The model's seven tools (`app/tools.py`) are the spec's, each one
  `GET /api/v1/ai/read/...`. `app/hub.py` has no write method. The hub's `AiTokenGuard` refuses
  the AI token on every other route and every other method.
- **As the user.** Each hub call carries the AI token plus the user's own access token in
  `X-On-Behalf-Of`; the hub answers as that user (org scope, capabilities, the same redaction:
  no hospital costs). A 403 or 404 reaches the model as `not_available`; a screen record the
  user may not see ends the request with "That information is not available to you." before
  the model is asked.
- **Never acts.** There is nothing to act with, and the system prompt forbids claiming an action.
- **No invented figures.** Every answer is checked (`app/numbers.py`): a number found in no
  tool result (nor in the question) earns one correction turn; if the rewrite still fails, the
  answer is withheld.
- **Bounded.** At most 6 tool calls per question.

## Chat ordering (S17, `app/chat.py`, `app/dates.py`)

- **Drafts only.** The service returns a draft. hospital-web shows it on a card, and only the
  user's click sends `POST /shortages` (`source=CHAT`, OPEN or DRAFT) to the hub with the
  user's own token. The AI token is refused on that route (hub test `test_chat_drafts.py`).
- **The model extracts, code decides.** One structured-output call (`output_config.format`,
  `app.chat.SCHEMA`, no tools) returns the product phrases, the figures with the user's words
  each came from, and the kind of date. Every figure's quote must be in the message and hold
  that figure ("2k" for 2000), or the field is dropped and asked for. Notes and questions pass
  the S13 number check. The model never computes a date or a shortfall.
- **Products.** Each phrase must be the user's own words; the hub scores it
  (`GET /ai/read/products/search`, as the user) against names, codes and
  `scripts/seed/synonyms.yaml`. Another product within 10% of the best: no product is chosen,
  the candidates and a question come back. Several products: the user is asked to split it.
- **Dates.** `app/dates.py` resolves the model's `{kind, weekday, day, month, amount,
time_of_day, clock_time}` in `user_tz` from `now`: weekdays and days of the month roll to the
  next one on or after today, "in N hours" is exact, an unstated time is 23:59 (and says so).
- **Untrusted text.** The message reaches the model inside `<chat_message>` tags, with `<`
  escaped, as data.

## Evals

`make eval-ai` runs `evals/copilot.jsonl` (15 Scenario 1 questions, `app/evals.py`) and then
`evals/chat_orders.jsonl` (20 phrasings, pass bar 18, `app/chat_evals.py`) against the real
model and a live hub; see `evals/README.md`. Each skips without a key.
