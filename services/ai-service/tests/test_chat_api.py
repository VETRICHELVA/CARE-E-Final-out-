"""POST /chat/draft through the FastAPI app (S17), and the service's lack of any write path."""

import inspect

import httpx
import pytest
from app.config import Settings
from app.hub import HubReader
from app.main import create_app

from tests.fakes import USER_TOKEN, FakeHub, FakeProvider, extraction, q, text, when

pytestmark = pytest.mark.anyio

NO_KEY = Settings(ai_api_key="", _env_file=None)  # type: ignore[call-arg]
AUTH = {"Authorization": f"Bearer {USER_TOKEN}"}
BODY = {
    "message": "need 850 SK-A by fri for ICU",
    "user_tz": "Asia/Kolkata",
    "now": "2026-10-05T04:30:00Z",
}
RAW = extraction(
    products=["SK-A"],
    qty_required=q(850, "850"),
    required_by=when("weekday", "by fri", weekday="friday"),
    notes="for ICU",
)


def client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://ai.test")  # type: ignore[arg-type]


def provider() -> FakeProvider:
    return FakeProvider(lambda conv: text(""), lambda user_text: RAW)


async def test_draft_returns_the_card_fields_and_the_reads_it_made() -> None:
    hub = FakeHub()
    async with client(create_app(NO_KEY, provider=provider(), hub=hub.reader())) as c:
        r = await c.post("/chat/draft", json=BODY, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["draft"]["product_code"] == "SURG-KIT-A"
    assert out["draft"]["required_by"] == "2026-10-09T23:59:00+05:30"
    assert out["draft"]["qty_required"] == 850
    assert out["missing_fields"] == ["priority", "min_shelf_life_days", "qty_local_usable"]
    assert out["product_candidates"] == [] and out["question"] is None
    (trace,) = out["tool_trace"]
    assert (trace["tool"], trace["input"], trace["ok"]) == ("search_products", {"q": "SK-A"}, True)
    assert [x.method for x in hub.requests] == ["GET"]


async def test_draft_without_a_key_or_a_user() -> None:
    hub = FakeHub()
    async with client(create_app(NO_KEY, hub=hub.reader())) as c:
        r = await c.post("/chat/draft", json=BODY, headers=AUTH)
        assert (r.status_code, r.json()["error"]) == (503, "ai_not_configured")
    async with client(create_app(NO_KEY, provider=provider(), hub=hub.reader())) as c:
        r = await c.post("/chat/draft", json=BODY)
        assert r.status_code == 401
    assert hub.requests == []


@pytest.mark.parametrize(
    "bad",
    [
        {"user_tz": "Mars/Olympus"},
        {"user_tz": ""},
        {"now": "2026-10-05T04:30:00"},  # no offset
        {"message": ""},
        {"message": "x" * 2001},
    ],
)
async def test_draft_validates_its_input(bad: dict[str, str]) -> None:
    async with client(create_app(NO_KEY, provider=provider(), hub=FakeHub().reader())) as c:
        r = await c.post("/chat/draft", json={**BODY, **bad}, headers=AUTH)
    assert r.status_code == 422, r.text


async def test_a_hub_that_refuses_the_user_is_a_401() -> None:
    hub = FakeHub({"products/search": (401, {"code": "unauthenticated", "message": "x"})})
    async with client(create_app(NO_KEY, provider=provider(), hub=hub.reader())) as c:
        r = await c.post("/chat/draft", json=BODY, headers=AUTH)
    assert r.status_code == 401


def test_the_ai_service_has_no_route_that_creates_anything() -> None:
    """Rule 2: the service drafts; only the user's own click sends POST /shortages."""
    app = create_app(NO_KEY, provider=provider(), hub=FakeHub().reader())
    routes = {
        (method, route.path)  # type: ignore[attr-defined]
        for route in app.routes
        for method in getattr(route, "methods", set())
        if not route.path.startswith(("/docs", "/redoc", "/openapi"))  # type: ignore[attr-defined]
    }
    assert routes == {
        ("GET", "/health"),
        ("GET", "/status"),
        ("POST", "/copilot/ask"),
        ("POST", "/chat/draft"),
    }
    # And its hub client can only GET /ai/read/*.
    methods = {n for n, _ in inspect.getmembers(HubReader, inspect.isfunction)}
    assert methods == {"__init__", "aclose", "get"}
