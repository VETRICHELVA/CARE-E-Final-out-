"""The copilot eval (`make eval-ai`; evals/README.md): 15 questions over Scenario 1, each with
expected facts, plus the S13 number check: an answer fails if it contains a number that none
of its tool results contains.

Needs a model key (AI_API_KEY or ANTHROPIC_API_KEY); without one it prints why and exits 0,
so CI and `make test` never need a key. With one it also needs the hub running at
HUB_API_URL (with the same AI_SERVICE_TOKEN) after `make migrate seed`: it drives Scenario 1
to steps 2, 4 and 7 with evals/scenario1_steps.py, run in services/hub-api so it uses the
hub's own settings (DATABASE_URL, JWT_SECRET), and asks each step's questions as the case's
user.

    cd services/ai-service && uv run python -m app.evals [--only c01,c15]
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app import copilot
from app.config import Settings, settings
from app.hub import HubReader
from app.llm import Provider, provider_from
from app.numbers import DIGIT_COMMA_RE, unsupported_numbers

SERVICE_DIR = Path(__file__).resolve().parents[1]
CASES = SERVICE_DIR / "evals" / "copilot.jsonl"
STEPS_SCRIPT = SERVICE_DIR / "evals" / "scenario1_steps.py"
HUB_DIR = SERVICE_DIR.parent / "hub-api"
NO_KEY = (
    "Skipping the copilot eval: no AI_API_KEY (or ANTHROPIC_API_KEY) is set. "
    "Set one to run it against a live hub (see services/ai-service/README.md)."
)


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def normalize(text: str) -> str:
    """evals/README.md, Text matching: lowercase, and no commas between digits."""
    return DIGIT_COMMA_RE.sub("", text.lower())


def resolve(context: dict[str, str], refs: dict[str, str]) -> dict[str, str]:
    """`$s1.shortage` -> the id of Scenario 1's shortage in this run, etc."""
    out = {}
    for key, value in context.items():
        if value.startswith("$s1."):
            name = value.removeprefix("$s1.")
            if name not in refs:
                raise KeyError(f"{value} is not available at this step")
            value = refs[name]
        out[key] = value
    return out


def grade(case: dict[str, Any], answer: str, tool_results: list[Any]) -> list[str]:
    """Every failure of `answer` against the case; empty means it passes."""
    text = normalize(answer)
    failures = []
    for fact in case["must_match"]:
        if not any(re.search(pattern, text) for pattern in fact):
            failures.append(f"missing fact {fact}")
    for pattern in case["must_not_match"]:
        if re.search(pattern, text):
            failures.append(f"must not match {pattern!r}")
    missing = unsupported_numbers(answer, tool_results)
    if missing:
        failures.append(f"numbers not in any tool result: {', '.join(missing)}")
    return failures


@dataclass
class CaseResult:
    id: str
    answer: str
    failures: list[str]
    tools: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def drive_scenario(step: int, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Run evals/scenario1_steps.py in the hub's environment (continuing from the `previous`
    step's state); its last line is the new state."""
    cmd = ["uv", "run", "python", str(STEPS_SCRIPT), "--to", str(step)]
    with tempfile.NamedTemporaryFile("w", suffix=".json") as state:
        if previous is not None:
            json.dump(previous, state)
            state.flush()
            cmd += ["--state", state.name]
        done = subprocess.run(cmd, cwd=HUB_DIR, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"Scenario 1 step {step} failed:\n{done.stderr[-2000:]}")
    lines = done.stdout.strip().splitlines()
    state_out: dict[str, Any] = json.loads(lines[-1])
    return state_out


async def run_case(
    case: dict[str, Any],
    state: dict[str, Any],
    hub: HubReader,
    provider: Provider,
    max_tool_calls: int = 6,
) -> CaseResult:
    result = await copilot.ask(
        case["question"],
        resolve(case["context"], state["refs"]),
        user_token=state["tokens"][case["as_user"]],
        hub=hub,
        provider=provider,
        max_tool_calls=max_tool_calls,
    )
    results = [t.result for t in result.tool_trace if t.ok]
    return CaseResult(
        case["id"],
        result.answer,
        grade(case, result.answer, results),
        [t.label for t in result.tool_trace],
    )


async def run(cfg: Settings, only: set[str] | None = None) -> list[CaseResult]:
    cases = [c for c in load_cases() if not only or c["id"] in only]
    hub = HubReader(cfg.hub_api_url, cfg.ai_service_token)
    provider = provider_from(cfg)
    results: list[CaseResult] = []
    state: dict[str, Any] | None = None
    try:
        for step in (2, 4, 7):
            state = await asyncio.to_thread(drive_scenario, step, state)
            for case in (c for c in cases if c["after_step"] == step):
                results.append(await run_case(case, state, hub, provider, cfg.ai_max_tool_calls))
                r = results[-1]
                print(f"{'PASS' if r.passed else 'FAIL'} {r.id}: {r.answer}")
                for failure in r.failures:
                    print(f"     - {failure}")
    finally:
        await hub.aclose()
    return results


def main(argv: list[str] | None = None, cfg: Settings = settings) -> int:
    parser = argparse.ArgumentParser(description="Run the copilot eval set.")
    parser.add_argument("--only", help="comma-separated case ids, e.g. c01,c15")
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
    print(f"\n{passed}/{len(results)} cases passed (model {cfg.ai_model}).")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
