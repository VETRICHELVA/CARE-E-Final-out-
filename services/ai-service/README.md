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

Without a key the service still starts: `GET /status` says `{"configured": false}` and
`POST /copilot/ask` answers 503 `{"error": "ai_not_configured"}`. Nothing else in CARE-E
depends on it.

## API

- `POST /copilot/ask` `{question, context: {shortage_id?, recommendation_id?, shipment_id?}}`
  with the signed-in user's hub access token as `Authorization: Bearer` →
  `{answer, tool_trace: [{tool, input, ok, label, result, from_context}]}`. `label` is the
  "Based on:" chip. 401 when the hub refuses the user's token (refresh and retry), 502 when the
  hub or the model is unreachable.
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

## Evals

`make eval-ai` runs `evals/copilot.jsonl` (15 Scenario 1 questions) against the real model and
a live hub; see `evals/README.md`. It skips without a key.
