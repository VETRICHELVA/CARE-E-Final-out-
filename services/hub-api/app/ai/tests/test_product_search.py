"""GET /ai/read/products/search (S17): the chat's product lookup, AI service token only."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tests.conftest import AiClientFor
from app.auth import service as auth_service
from app.catalog.models import Product
from app.config import settings
from app.conftest import ClientFor, World

pytestmark = pytest.mark.anyio

URL = "/ai/read/products/search"


async def test_search_scores_name_code_and_synonyms(
    ai_client_for: AiClientFor, world: World, products: dict[str, Product]
) -> None:
    ai = await ai_client_for(world.users["a.REQUESTER"])
    r = await ai.get(URL, params={"q": "SK-A"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["q"] == "SK-A"
    best = out["items"][0]
    kit = products["SURG-KIT-A"]
    assert best == {
        "product_id": str(kit.id),
        "code": "SURG-KIT-A",
        "name": "Surgical Kit A",
        "category": "Surgical",
        "unit": "kit",
        "requires_cold_chain": False,
        "default_min_shelf_life_days": 30,
        "score": 1.0,
        "matched_on": "SK-A",
    }


async def test_plain_kits_returns_both_kits_close_together(
    ai_client_for: AiClientFor, world: World, products: dict[str, Product]
) -> None:
    ai = await ai_client_for(world.users["b.STORE_MANAGER"])  # any org reads the catalog
    items = (await ai.get(URL, params={"q": "kits"})).json()["items"]
    top = {i["code"]: i["score"] for i in items[:2]}
    assert set(top) == {"SURG-KIT-A", "DIAG-RDK"}
    assert abs(top["SURG-KIT-A"] - top["DIAG-RDK"]) <= 0.1 * max(top.values())


async def test_limit_and_no_match(
    ai_client_for: AiClientFor, world: World, products: dict[str, Product]
) -> None:
    ai = await ai_client_for(world.users["a.REQUESTER"])
    assert len((await ai.get(URL, params={"q": "gloves", "limit": 1})).json()["items"]) == 1
    assert (await ai.get(URL, params={"q": "ward 4"})).json()["items"] == []
    assert (await ai.get(URL, params={"q": ""})).status_code == 422
    assert (await ai.get(URL, params={"q": "kit", "limit": 21})).status_code == 422


async def test_search_needs_the_ai_token_and_a_user(
    session: AsyncSession, client_for: ClientFor, world: World, products: dict[str, Product]
) -> None:
    user = await client_for(world.users["a.REQUESTER"])  # a user token alone is refused
    r = await user.get(URL, params={"q": "SK-A"})
    assert (r.status_code, r.json()["code"]) == (401, "unauthenticated")
    ai = await client_for()
    ai.headers["Authorization"] = f"Bearer {settings.ai_service_token}"
    assert (await ai.get(URL, params={"q": "SK-A"})).status_code == 401  # nobody on behalf of
    pair = await auth_service.issue_tokens(session, world.users["a.REQUESTER"])
    r = await ai.get(URL, params={"q": "SK-A"}, headers={"X-On-Behalf-Of": pair.access_token})
    assert r.status_code == 200
    # Read-only: no other method opens it for the AI token.
    r = await ai.post(URL, params={"q": "SK-A"}, headers={"X-On-Behalf-Of": pair.access_token})
    assert r.status_code == 401
