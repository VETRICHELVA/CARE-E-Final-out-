# S13 — AI service and copilot

**Milestone:** M2 · **Depends on:** S12 · **Workstream:** AI/ML · **Can run alongside:** S14, S15, S16

## Read first
- `CLAUDE.md` — Non-negotiable rule 2
- `docs/specs/apps-ai-iot.md` — AI service; Copilot
- `docs/specs/api-and-events.md` — S13 row

## Goal
Hospital users can ask "why" questions about a shortage, its sources or its shipment, and get answers built only from hub data, with the sources shown. The AI cannot change anything.

## Build
- **Hub:**
  - `GET /api/v1/ai/read/*` endpoints backing each copilot tool.
  - Callable only with a **service token** carrying the scope `ai.read`, plus an `X-On-Behalf-Of: <user_id>` header. The hub applies that user's org scope, so the AI sees only what the user could see.
  - A middleware test proves the service token is refused on every non-`ai/read` route.
- **`services/ai-service`:**
  - FastAPI app with a provider abstraction configured by `AI_PROVIDER`, `AI_MODEL` and `AI_API_KEY`.
  - Tool definitions from the spec and a system prompt enforcing: answer only from tool results; quote numbers exactly; say when data is missing; never claim an action was taken.
  - A tool loop capped at 6 calls.
  - `POST /copilot/ask {question, context}` returns `{answer, tool_trace}`.
  - With no API key, it returns `{"error": "ai_not_configured"}`.
- **hospital-web:** a chat panel on every page (copilot mode), sending the current screen's IDs as context and showing "Based on:" chips from `tool_trace`.
- **Evaluation:**
  - `services/ai-service/evals/copilot.jsonl`: 15 questions over the Scenario 1 data, each with expected facts.
  - A checker fails any answer that contains a number not present in the tool results.
  - Unit tests use a deterministic fake LLM; the real-model eval runs with `make eval-ai` when a key is set.

## Out of scope
Chat ordering (S17).

## Acceptance criteria
- [ ] "Why was Hospital D rejected?" → the answer states 12 days vs 30 days required (eval).
- [ ] Across the eval set, no answer contains a number missing from its tool results.
- [ ] The service token cannot call any write or non-`ai/read` endpoint (test).
- [ ] A Hospital B user asking about Hospital A's shortage gets "not available", not data.
- [ ] With no API key, the panel shows "AI is not configured" and nothing else breaks.

## Verify
```
make test-hub && (cd services/ai-service && uv run pytest) && make test-web
make eval-ai   # optional, needs AI_API_KEY
```
