"""The chat-ordering eval (`make eval-ai`; evals/README.md, chat_orders.jsonl): 20 phrasings,
each graded as a correct draft, a correct clarifying question or a correctly reported missing
field. The pass bar is 18 of 20 (S17).

Needs a model key (AI_API_KEY or ANTHROPIC_API_KEY); without one it prints why and exits 0.
With one it also needs the hub at HUB_API_URL (same AI_SERVICE_TOKEN) after
`make migrate seed`: it signs in as CHAT_EVAL_USER (default requester@hospital-a.demo, password
SEED_PASSWORD, default the seed's), maps product codes to ids with GET /products, and drafts
each message through app.chat exactly as POST /chat/draft does, as that user.

    cd services/ai-service && uv run python -m app.chat_evals [--only o01,o14]
"""

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app import chat
from app.config import Settings, settings
from app.evals import normalize
from app.hub import HubReader
from app.llm import provider_from

CASES = Path(__file__).resolve().parents[1] / "evals" / "chat_orders.jsonl"
PASS_BAR = 18
NO_KEY = (
    "Skipping the chat-ordering eval: no AI_API_KEY (or ANTHROPIC_API_KEY) is set. "
    "Set one to run it against a live hub (see services/ai-service/README.md)."
)
DRAFT_FIELDS = ("qty_required", "qty_local_usable", "priority", "min_shelf_life_days")


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def as_json(result: chat.ChatResult) -> dict[str, Any]:
    """What POST /chat/draft returns for `result` (without the trace)."""
    return {
        "draft": result.draft.model_dump() if result.draft else None,
        "missing_fields": result.missing_fields,
        "product_candidates": [c.model_dump() for c in result.product_candidates],
        "question": result.question,
    }


def _check_draft(
    expect: dict[str, Any], d: dict[str, Any], tz: str, catalog: dict[str, dict[str, Any]]
) -> list[str]:
    failures = []
    if "product" in expect:
        want = catalog[expect["product"]]["id"]
        if d.get("product_id") != want:
            failures.append(f"product {d.get('product_code')} != {expect['product']}")
    for f in DRAFT_FIELDS:
        if f not in expect:
            continue
        want = expect[f]
        if want == "$product_default":
            want = catalog[expect["product"]]["default_min_shelf_life_days"]
        if d.get(f) != want:
            failures.append(f"{f} {d.get(f)!r} != {want!r}")
    if "required_by" in expect:
        got = d.get("required_by")
        at = datetime.fromisoformat(got) if got else None
        spec = expect["required_by"]
        if "instant" in spec:
            if at is None or at != datetime.fromisoformat(spec["instant"]):
                failures.append(f"required_by {got} != {spec['instant']}")
        elif at is None or at.astimezone(ZoneInfo(tz)).date() != date.fromisoformat(
            spec["local_date"]
        ):
            failures.append(f"required_by {got} not on {spec['local_date']} in {tz}")
    if "notes_match" in expect and not re.search(
        expect["notes_match"], normalize(d.get("notes") or "")
    ):
        failures.append(f"notes {d.get('notes')!r} !~ {expect['notes_match']!r}")
    return failures


def grade(
    case: dict[str, Any], out: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> list[str]:
    """Every failure of `out` (a POST /chat/draft body) against the case (evals/README.md);
    empty means it passes. `catalog`: code -> {id, default_min_shelf_life_days}."""
    expect, d = case["expect"], out.get("draft")
    question = (out.get("question") or "").strip()
    missing = set(out.get("missing_fields") or [])
    match expect["kind"]:
        case "draft":
            if d is None:
                return ["no draft"]
            failures = _check_draft(expect, d, case["user_tz"], catalog)
            for name in expect.get("missing_fields_include", []):
                if name not in missing:
                    failures.append(f"{name} not in missing_fields")
            return failures
        case "clarify":
            failures = []
            if not question:
                failures.append("no question")
            if d is not None and d.get("product_id") is not None:
                failures.append(f"a product was chosen: {d.get('product_code')}")
            codes = {c.get("code") for c in out.get("product_candidates") or []}
            for code in expect.get("product_candidates_include", []):
                if code not in codes:
                    failures.append(f"{code} not among the candidates")
            return failures
        case "missing_field":
            fields = expect["fields"]
            failures = [
                f"{f} has a value" for f in fields if d is not None and d.get(f) is not None
            ]
            if not question and not set(fields) <= missing:
                failures.append("neither a question nor every field in missing_fields")
            if d is not None:
                failures += _check_draft(
                    {k: v for k, v in expect.items() if k in ("product", "priority")},
                    d,
                    case["user_tz"],
                    catalog,
                )
            return failures
    return [f"unknown kind {expect['kind']}"]


@dataclass
class CaseResult:
    id: str
    out: dict[str, Any]
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def sign_in(cfg: Settings) -> tuple[str, dict[str, dict[str, Any]]]:
    """The eval user's access token and the catalog (code -> id and default shelf life)."""
    email = os.environ.get("CHAT_EVAL_USER", "requester@hospital-a.demo")
    password = os.environ.get("SEED_PASSWORD", "demo1234")
    base = cfg.hub_api_url.rstrip("/")
    r = httpx.post(f"{base}/auth/login", json={"email": email, "password": password}, timeout=10)
    r.raise_for_status()
    token = r.json()["access_token"]
    catalog: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 100, **({"cursor": cursor} if cursor else {})}
        page = httpx.get(
            f"{base}/products",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        page.raise_for_status()
        body = page.json()
        for p in body["items"]:
            catalog[p["code"]] = p
        cursor = body.get("next_cursor")
        if not cursor:
            return token, catalog


async def run(cfg: Settings, only: set[str] | None = None) -> list[CaseResult]:
    cases = [c for c in load_cases() if not only or c["id"] in only]
    token, catalog = await asyncio.to_thread(sign_in, cfg)
    hub = HubReader(cfg.hub_api_url, cfg.ai_service_token)
    provider = provider_from(cfg)
    results: list[CaseResult] = []
    try:
        for case in cases:
            result = await chat.draft(
                case["message"],
                user_tz=case["user_tz"],
                now=datetime.fromisoformat(case["now"]),
                user_token=token,
                hub=hub,
                provider=provider,
            )
            out = as_json(result)
            r = CaseResult(case["id"], out, grade(case, out, catalog))
            results.append(r)
            d = out["draft"] or {}
            summary = {k: d.get(k) for k in ("product_code", "qty_required", "required_by")}
            print(f"{'PASS' if r.passed else 'FAIL'} {r.id}: {case['message']!r}")
            print(f"     -> {summary} question={out['question']!r}")
            for failure in r.failures:
                print(f"     - {failure}")
    finally:
        await hub.aclose()
    return results


def main(argv: list[str] | None = None, cfg: Settings = settings) -> int:
    parser = argparse.ArgumentParser(description="Run the chat-ordering eval set.")
    parser.add_argument("--only", help="comma-separated case ids, e.g. o01,o14")
    args = parser.parse_args(argv)
    if not cfg.configured:
        print(NO_KEY)
        return 0
    try:
        httpx.get(cfg.hub_api_url.rsplit("/api/", 1)[0] + "/health", timeout=5).raise_for_status()
    except httpx.HTTPError:
        print(f"The hub is not reachable at {cfg.hub_api_url}; start it first.")
        return 2
    only = set(args.only.split(",")) if args.only else None
    results = asyncio.run(run(cfg, only))
    passed = sum(r.passed for r in results)
    bar = PASS_BAR if not only else len(results)
    print(f"\n{passed}/{len(results)} chat phrasings passed (bar {bar}; model {cfg.ai_model}).")
    return 0 if passed >= bar else 1


if __name__ == "__main__":
    sys.exit(main())
