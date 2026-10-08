"""POST /copilot/ask and GET /status, through the FastAPI app."""

import httpx
import pytest
from app.config import Settings
from app.main import create_app

from tests.fakes import OTHER, SHORTAGE, USER_TOKEN, FakeHub, FakeProvider, text

pytestmark = pytest.mark.anyio

NO_KEY = Settings(ai_api_key="", _env_file=None)  # type: ignore[call-arg]


def client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://ai.test")  # type: ignore[arg-type]


async def test_without_a_key_the_copilot_says_it_is_not_configured() -> None:
    hub = FakeHub()
    async with client(create_app(NO_KEY, hub=hub.reader())) as c:
        r = await c.post(
            "/copilot/ask",
            json={"question": "Why?", "context": {"shortage_id": SHORTAGE}},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
        assert r.status_code == 503
        assert r.json()["error"] == "ai_not_configured"
        assert (await c.get("/status")).json() == {"configured": False, "model": None}
        assert (await c.get("/health")).json() == {"status": "ok"}
    assert hub.requests == []  # nothing was read either


def test_a_key_from_either_variable_configures_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not Settings(_env_file=None).configured  # type: ignore[call-arg]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert Settings(_env_file=None).configured  # type: ignore[call-arg]
    monkeypatch.setenv("AI_API_KEY", "")  # a blank line in .env does not hide it
    assert Settings(_env_file=None).ai_api_key == "sk-test"  # type: ignore[call-arg]
    monkeypatch.setenv("AI_PROVIDER", "other")
    assert not Settings(_env_file=None).configured  # type: ignore[call-arg]


async def test_ask_returns_the_answer_and_its_tool_trace() -> None:
    provider = FakeProvider(lambda conv: text("The shortfall is 850."))
    app = create_app(NO_KEY, provider=provider, hub=FakeHub().reader())
    async with client(app) as c:
        r = await c.post(
            "/copilot/ask",
            json={"question": "What is our shortfall?", "context": {"shortage_id": SHORTAGE}},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answer"] == "The shortfall is 850."
    (entry,) = out["tool_trace"]
    assert entry["tool"] == "get_shortage" and entry["label"] == "shortage: Surgical Kit A"
    assert entry["from_context"] is True and entry["result"]["shortfall"] == 850


async def test_another_orgs_shortage_answers_not_available() -> None:
    app = create_app(NO_KEY, provider=FakeProvider(lambda c: text("12")), hub=FakeHub().reader())
    async with client(app) as c:
        r = await c.post(
            "/copilot/ask",
            json={"question": "Why was Hospital D rejected?", "context": {"shortage_id": OTHER}},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
    assert r.status_code == 200
    assert r.json()["answer"] == "That information is not available to you."


async def test_auth_errors() -> None:
    expired = FakeHub({f"shortages/{SHORTAGE}": (401, {"code": "unauthenticated"})})
    app = create_app(NO_KEY, provider=FakeProvider(lambda c: text("x")), hub=expired.reader())
    async with client(app) as c:
        body = {"question": "Why?", "context": {"shortage_id": SHORTAGE}}
        r = await c.post("/copilot/ask", json=body)
        assert r.status_code == 401 and r.json()["error"] == "unauthenticated"
        r = await c.post("/copilot/ask", json=body, headers={"Authorization": "Bearer stale"})
        assert r.status_code == 401  # the browser refreshes its token and retries
        r = await c.post("/copilot/ask", json={"question": ""}, headers={"Authorization": "x"})
        assert r.status_code == 422


async def test_hub_down() -> None:
    down = FakeHub({f"shortages/{SHORTAGE}": (503, {})})
    app = create_app(NO_KEY, provider=FakeProvider(lambda c: text("x")), hub=down.reader())
    async with client(app) as c:
        r = await c.post(
            "/copilot/ask",
            json={"question": "Why?", "context": {"shortage_id": SHORTAGE}},
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
        )
    assert r.status_code == 502 and r.json()["error"] == "hub_unavailable"
