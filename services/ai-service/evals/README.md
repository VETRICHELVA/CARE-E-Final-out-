# AI eval sets

S13 built the copilot runner (`make eval-ai`, `app/evals.py`); S17 adds the chat-ordering one.

| File                | Section | Cases | Pass bar                                |
| ------------------- | ------- | ----- | --------------------------------------- |
| `copilot.jsonl`     | S13     | 15    | every case, plus the S13 number check   |
| `chat_orders.jsonl` | S17     | 20    | at least 18 correct drafts or questions |

One JSON object per line. Products are keyed by catalog **code** (`SURG-KIT-A`, `DIAG-RDK`,
`IV-CAN-20G`; demo-scenarios.md); the runner maps a code to its id with `GET /products`. The
user's own words in `message` and `question` keep whatever names they use.

## Text matching (both files)

Before matching, lowercase the text and remove commas between digits (`1,000` → `1000`). A
**fact** is a list of regexes; it passes if any one matches (`re.search`).

## copilot.jsonl (S13)

| Field            | Meaning                                                                    |
| ---------------- | -------------------------------------------------------------------------- |
| `id`             | `c01`…`c15`                                                                |
| `after_step`     | Scenario 1 step (`demo-scenarios.md`) the hub must have reached: 2, 4 or 7 |
| `as_user`        | Demo user the question is asked on behalf of (`X-On-Behalf-Of`)            |
| `context`        | Screen IDs sent with the question; `$s1.*` refs are resolved by the runner |
| `question`       | Text sent to `POST /copilot/ask`                                           |
| `must_match`     | List of facts; every fact must pass against `answer`                       |
| `must_not_match` | Regexes that must not match `answer`                                       |
| `source`         | The spec line the expected facts come from                                 |

- Refs: `$s1.shortage` (Hospital A's `SURG-KIT-A` shortage), `$s1.recommendation` (the BUY
  recommendation after step 4), `$s1.shipment` (the Supplier Y shipment).
- `scenario1_steps.py` drives the hub there through its own services (run in the hub's
  environment against the hub's database): `--to 2` prepares Scenario 1 (`e2e/seed/scenario1.py`,
  which adds missing seed rows but never resets stock: start from `make demo-reset`) and reports
  the shortage; `--to 4` declines B's request **without** a reason (so B's decline
  has `reason_source=SYSTEM` and c08 sees "No reason was entered.") and waits for the BUY;
  `--to 7` approves it, Supplier Y acknowledges and dispatches, SwiftMed delivers, and Hospital A
  receives and accepts 790 (residual 60). Each prints `{step, refs, tokens}`, the tokens being
  access tokens for the `as_user` accounts, which the runner sends as the user's token.
- Runner: `make eval-ai` (or `cd services/ai-service && uv run python -m app.evals [--only c01]`).
  With no `AI_API_KEY`/`ANTHROPIC_API_KEY` it prints why it is skipping and exits 0. Otherwise it
  needs the hub running at `HUB_API_URL` with the same `AI_SERVICE_TOKEN` (after
  `make migrate seed`); the steps run in `services/hub-api` with the hub's own settings. It prints PASS/FAIL per case and the
  score, and exits 1 unless every case passes.
- Expected facts use only exact figures from the spec, never the "about" ETAs or costs.
- Separately, every answer fails if it contains a number not present in its tool results (S13).

## chat_orders.jsonl (S17)

| Field     | Meaning                                                 |
| --------- | ------------------------------------------------------- |
| `id`      | `o01`…`o20`                                             |
| `message` | Text sent to `POST /chat/draft`                         |
| `user_tz` | IANA zone sent as `user_tz`                             |
| `now`     | Fixed UTC instant sent as `now`; all dates anchor on it |
| `expect`  | What a correct response looks like (below)              |

Anchors: `2026-10-05T04:30:00Z` is Monday 10:00 in Asia/Kolkata; `2026-10-08T20:00:00Z` is
Thursday 20:00 UTC but already Friday 01:30 in Asia/Kolkata (o12/o13 test that "tomorrow" differs).

### `expect.kind`

- **`draft`**: `draft` is not null and every listed field matches:
  - `product`: `draft.product_id` is that product's id.
  - `qty_required`, `qty_local_usable`, `priority`, `min_shelf_life_days`: equal.
    `"$product_default"` means the product's `default_min_shelf_life_days` from the catalog.
  - `required_by`: `{"instant": ISO}` must equal exactly (explicit time or offset);
    `{"local_date": "YYYY-MM-DD"}` must fall on that date in `user_tz` (time of day not graded).
  - `notes_match`: regex that must match `draft.notes`.
  - `missing_fields_include`: each name must be in `missing_fields`.
- **`clarify`**: `question` is non-empty and no product is chosen (`draft` or `draft.product_id`
  is null). `product_candidates_include`, if present, must all be in `product_candidates`.
  `reason` (`ambiguous_product`, `multiple_products`, `no_product`) is a label, not graded.
- **`missing_field`**: no name in `fields` has a non-null value in `draft`, and either `question`
  is non-empty or every name in `fields` is in `missing_fields`. If `draft` is not null, the other
  listed fields (`product`, `priority`) must match as for `draft`.

- Runner (S17): `make eval-ai` runs it after the copilot set (or
  `cd services/ai-service && uv run python -m app.chat_evals [--only o01,o14]`). With no
  `AI_API_KEY`/`ANTHROPIC_API_KEY` it prints why it is skipping and exits 0. Otherwise it needs
  the hub at `HUB_API_URL` (same `AI_SERVICE_TOKEN`, after `make migrate seed`): it signs in as
  `CHAT_EVAL_USER` (default `requester@hospital-a.demo`, password `SEED_PASSWORD`, default the
  seed's), maps codes with `GET /products`, drafts each message through `app.chat` exactly as
  `POST /chat/draft` does, prints PASS/FAIL per case and the score, and exits 1 under 18 of 20
  (with `--only`, unless every listed case passes). `draft.required_by` is ISO 8601 with the
  user's offset; candidates are matched by `code`.
- `tests/test_chat_evals.py` replays all 20 offline with the extraction a correct model would
  return, so the real run measures the model alone.

Unlisted fields are not graded. Priority: "urgent" (or "urgently"), "critical" and "emergency" mean CRITICAL;
anything else defaults to ROUTINE.
