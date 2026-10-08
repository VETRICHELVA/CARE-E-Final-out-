"""Deterministic stand-ins: a scripted model and a hub behind httpx.MockTransport. No test
needs an API key or a running hub."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from app.hub import HubReader
from app.llm import Conversation, ModelToolUse, ModelTurn, ToolOutcome

AI_TOKEN = "test-ai-token"
USER_TOKEN = "user-access-token"
SHORTAGE = "11111111-1111-4111-8111-111111111111"
SHIPMENT = "22222222-2222-4222-8222-222222222222"
OTHER = "99999999-9999-4999-8999-999999999999"


@dataclass
class Step:
    """What the fake model was given at one step."""

    outcomes: list[ToolOutcome]
    followup: str | None
    allow_tools: bool


Brain = Callable[["FakeConversation"], ModelTurn]


@dataclass
class FakeConversation:
    system: str
    user_text: str
    tools: list[dict[str, Any]]
    brain: Brain
    steps: list[Step] = field(default_factory=list)

    async def step(
        self,
        tool_outcomes: list[ToolOutcome] | None = None,
        *,
        followup: str | None = None,
        allow_tools: bool = True,
    ) -> ModelTurn:
        self.steps.append(Step(list(tool_outcomes or []), followup, allow_tools))
        turn = self.brain(self)
        if turn.tool_uses and not allow_tools:
            raise AssertionError("the model called a tool after tools were turned off")
        return turn

    @property
    def last(self) -> Step:
        return self.steps[-1]

    def results(self) -> list[Any]:
        return [json.loads(o.content) for s in self.steps for o in s.outcomes]


class FakeProvider:
    def __init__(self, brain: Brain) -> None:
        self.brain = brain
        self.conversations: list[FakeConversation] = []

    def start(self, system: str, user_text: str, tools: list[dict[str, Any]]) -> Conversation:
        conversation = FakeConversation(system, user_text, tools, self.brain)
        self.conversations.append(conversation)
        return conversation


def use(name: str, **args: Any) -> ModelToolUse:
    return ModelToolUse(id=f"tu_{name}_{len(args)}", name=name, input=args)


def text(answer: str) -> ModelTurn:
    return ModelTurn(text=answer)


def tool_turn(*uses: ModelToolUse) -> ModelTurn:
    return ModelTurn(text="", tool_uses=list(uses))


# --- the hub ------------------------------------------------------------------------------------

SHORTAGE_BODY = {
    "id": SHORTAGE,
    "product_code": "SURG-KIT-A",
    "product_name": "Surgical Kit A",
    "qty_required": 1000,
    "qty_local_usable": 150,
    "shortfall": 850,
    "priority": "CRITICAL",
    "status": "MATCHING",
    "source_requests": [],
}
MATCH_RUN_BODY = {
    "run_no": 1,
    "shortage_id": SHORTAGE,
    "candidates": [
        {
            "source_org_name": "Hospital B",
            "eligible": True,
            "rank": 1,
            "transferable_qty": 1000,
            "eta_hours": 5.833333,
            "landed_cost_paise": None,
            "gate_results": [],
        },
        {
            "source_org_name": "Hospital D",
            "eligible": False,
            "rank": None,
            "transferable_qty": 900,
            "eta_hours": 4.1,
            "landed_cost_paise": None,
            "gate_results": [
                {"gate": "shelf_life", "passed": False, "reason": "Expires in 12 days; 30 required"}
            ],
        },
        {
            "source_org_name": "Supplier Y",
            "eligible": True,
            "rank": 2,
            "offered_qty": 2000,
            "eta_hours": 24.25,
            "landed_cost_paise": 2400000,
            "gate_results": [],
        },
    ],
}


class FakeHub:
    """Routes GET /api/v1/ai/read/* to canned bodies and records every request."""

    def __init__(self, routes: dict[str, tuple[int, Any]] | None = None) -> None:
        self.routes = routes if routes is not None else default_routes()
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v1/ai/read/")
        for pattern, (status, body) in self.routes.items():
            if re.fullmatch(pattern, path):
                return httpx.Response(status, json=body)
        return httpx.Response(
            404, json={"code": "not_found", "message": "Not found.", "details": {}}
        )

    def reader(self) -> HubReader:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return HubReader("http://hub.test/api/v1", AI_TOKEN, client=client)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def default_routes() -> dict[str, tuple[int, Any]]:
    forbidden = {"code": "forbidden", "message": "Another org's.", "details": {}}
    return {
        f"shortages/{SHORTAGE}": (200, SHORTAGE_BODY),
        f"shortages/{SHORTAGE}/match-run": (200, MATCH_RUN_BODY),
        f"shortages/{OTHER}": (403, forbidden),
        f"shortages/{OTHER}/match-run": (403, forbidden),
        f"shipments/{SHIPMENT}": (200, {"id": SHIPMENT, "status": "RECONCILED", "qty": 850}),
        f"shipments/{SHIPMENT}/coldchain": (
            200,
            {"requires_cold_chain": False, "reading_count": 0, "events": []},
        ),
        "audit": (200, {"items": [], "next_cursor": None}),
    }
