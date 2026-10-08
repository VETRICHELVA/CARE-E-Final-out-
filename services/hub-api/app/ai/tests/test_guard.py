"""The AI service token (scope `ai.read`) opens GET /ai/read/* and nothing else, and those
endpoints open only for it, on behalf of a signed-in user.

Walks every endpoint in the real app's OpenAPI document, so endpoints added later are covered
automatically."""

import re
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.guard import REFUSED
from app.auth import service as auth_service
from app.config import settings
from app.conftest import ClientFor, World
from app.iot.tests.test_ingest_token import endpoints
from app.shortages.models import Shortage

pytestmark = pytest.mark.anyio

API = "/api/v1"
AI_READ = f"{API}/ai/read/"


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path).removeprefix(API)


async def test_the_ai_token_is_refused_on_every_non_ai_read_endpoint(
    app: FastAPI, session: AsyncSession, client_for: ClientFor, world: World
) -> None:
    client = await client_for()
    client.headers["Authorization"] = f"Bearer {settings.ai_service_token}"
    # Even with a valid user token on behalf of: the AI never acts outside /ai/read.
    pair = await auth_service.issue_tokens(session, world.users["a.ADMIN"])
    client.headers["X-On-Behalf-Of"] = pair.access_token
    checked = []
    for method, path in endpoints(app):
        if path.startswith(AI_READ):
            continue
        body: dict[str, object] | None = None if method in ("GET", "DELETE") else {}
        r = await client.request(method, concrete(path), json=body)
        assert r.status_code == 401, (method, path, r.status_code, r.text)
        assert r.json() == {"code": "unauthenticated", "message": REFUSED, "details": {}}
        checked.append((method, path))

    writes = [c for c in checked if c[0] != "GET"]
    assert len(checked) >= 50 and len(writes) >= 25  # login, every state change, webhooks...
    assert ("POST", f"{API}/recommendations/{{recommendation_id}}/approve") in checked
    assert ("POST", f"{API}/shortages") in checked
    assert ("POST", f"{API}/auth/login") in checked


async def test_the_ai_token_cannot_write_even_under_ai_read(
    client_for: ClientFor, shortage: Shortage
) -> None:
    client = await client_for()
    client.headers["Authorization"] = f"Bearer {settings.ai_service_token}"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        r = await client.request(method, f"/ai/read/shortages/{shortage.id}")
        assert r.status_code == 401, (method, r.text)
        assert r.json()["message"] == REFUSED


async def test_every_ai_read_endpoint_is_a_get(app: FastAPI) -> None:
    ai = [(m, p) for m, p in endpoints(app) if p.startswith(AI_READ)]
    assert len(ai) == 7  # one per copilot tool (apps-ai-iot.md)
    assert {m for m, _ in ai} == {"GET"}


async def test_ai_read_needs_the_ai_token_and_a_user(
    session: AsyncSession, client_for: ClientFor, world: World, shortage: Shortage
) -> None:
    url = f"/ai/read/shortages/{shortage.id}"
    user = await client_for(world.users["a.APPROVER"])  # a user token alone
    r = await user.get(url)
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"

    ingest = await client_for()
    ingest.headers["Authorization"] = f"Bearer {settings.ingest_token}"
    assert (await ingest.get(url)).status_code == 401

    ai = await client_for()
    ai.headers["Authorization"] = f"Bearer {settings.ai_service_token}"
    r = await ai.get(url)  # nobody on behalf of
    assert r.status_code == 401 and "X-On-Behalf-Of" in r.json()["message"]
    for bad in ("not-a-jwt", settings.ai_service_token, settings.ingest_token):
        r = await ai.get(url, headers={"X-On-Behalf-Of": bad})
        assert r.status_code == 401, bad

    wrong = await client_for()
    wrong.headers["Authorization"] = f"Bearer {settings.ai_service_token}x"
    pair = await auth_service.issue_tokens(session, world.users["a.APPROVER"])
    r = await wrong.get(url, headers={"X-On-Behalf-Of": pair.access_token})
    assert r.status_code == 401

    # A stream ticket is not an access token, so it names nobody either.
    ticket, _ = auth_service.issue_stream_ticket(
        auth_service.decode_access_token(pair.access_token)
    )
    r = await ai.get(url, headers={"X-On-Behalf-Of": ticket})
    assert r.status_code == 401

    ok = await ai.get(url, headers={"X-On-Behalf-Of": f"Bearer {pair.access_token}"})
    assert ok.status_code == 200, ok.text


async def test_a_logged_out_user_is_no_longer_represented(
    session: AsyncSession, client_for: ClientFor, world: World, shortage: Shortage
) -> None:
    pair = await auth_service.issue_tokens(session, world.users["a.APPROVER"])
    await auth_service.logout(session, pair.refresh_token)
    ai = await client_for()
    ai.headers["Authorization"] = f"Bearer {settings.ai_service_token}"
    r = await ai.get(
        f"/ai/read/shortages/{shortage.id}", headers={"X-On-Behalf-Of": pair.access_token}
    )
    assert r.status_code == 401
