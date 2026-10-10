"""Chat ordering's writes (S17): only the user's own POST /shortages (source=CHAT, OPEN or
DRAFT) and POST /shortages/{id}/confirm (DRAFT -> OPEN, business-rules §8). The AI service
token opens neither."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.guard import REFUSED
from app.auth import service as auth_service
from app.catalog.models import Product
from app.config import settings
from app.conftest import ClientFor, World
from app.domain.shortage import Status
from app.shortages.models import MatchRun, Shortage
from app.shortages.tests.test_shortages import audit_actions, body, facility_of, set_status

pytestmark = pytest.mark.anyio


@pytest.fixture
async def requester_a(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["a.REQUESTER"])


async def chat_body(session: AsyncSession, world: World, product: Product, **kw: Any) -> Any:
    return body(await facility_of(session, world.hospital_a), product, source="CHAT", **kw)


async def runs(session: AsyncSession, shortage_id: str) -> int:
    stmt = select(func.count()).where(MatchRun.shortage_id == uuid.UUID(shortage_id))
    return int(await session.scalar(stmt) or 0)


async def test_create_from_chat_is_audited_as_chat_by_the_user(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"], qty_local_usable=0)
    r = await requester_a.post("/shortages", json=payload)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["source"], out["status"], out["shortfall"]) == ("CHAT", "MATCHING", 1000)
    assert out["created_by"] == str(world.users["a.REQUESTER"].id)
    created = (await audit_actions(session, out["id"]))[0]
    assert created.action == "shortage.created"
    assert created.after is not None and created.after["source"] == "CHAT"
    assert created.actor_id == world.users["a.REQUESTER"].id
    assert await runs(session, out["id"]) == 1


async def test_save_as_draft_stores_it_unmatched(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["DIAG-RDK"], status="DRAFT")
    r = await requester_a.post("/shortages", json=payload)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["status"], out["source"], out["shortfall"]) == ("DRAFT", "CHAT", 850)
    assert await runs(session, out["id"]) == 0
    rows = await audit_actions(session, out["id"])
    assert [(x.action, x.actor_id) for x in rows] == [
        ("shortage.created", world.users["a.REQUESTER"].id)
    ]
    assert rows[0].after is not None and rows[0].after["status"] == "DRAFT"


async def test_a_draft_still_gets_the_hub_shortfall_rules(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(
        session, world, products["SURG-KIT-A"], status="DRAFT", qty_required=100,
        qty_local_usable=100, shortfall=5,
    )  # fmt: skip
    r = await requester_a.post("/shortages", json=payload)
    assert (r.status_code, r.json()["code"]) == (400, "validation")
    for status in ("MATCHING", "CANCELLED"):  # only OPEN or DRAFT may be asked for
        bad = await chat_body(session, world, products["SURG-KIT-A"], status=status)
        assert (await requester_a.post("/shortages", json=bad)).status_code == 422
    assert await session.scalar(select(func.count()).select_from(Shortage)) == 0


async def test_confirm_moves_a_draft_to_open_then_matching(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"], status="DRAFT")
    draft = (await requester_a.post("/shortages", json=payload)).json()
    r = await requester_a.post(f"/shortages/{draft['id']}/confirm", json={"reason": "Checked"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "MATCHING"
    assert await runs(session, draft["id"]) == 1
    changes = [
        (x.before, x.after, x.reason_source, x.actor_id)
        for x in await audit_actions(session, draft["id"])
        if x.action == "shortage.status_changed"
    ]
    user = world.users["a.REQUESTER"].id
    assert changes == [
        ({"status": "DRAFT"}, {"status": "OPEN"}, "USER", user),
        ({"status": "OPEN"}, {"status": "MATCHING"}, "SYSTEM", None),
    ]


async def test_confirm_anything_but_a_draft_is_409(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"])
    open_one = (await requester_a.post("/shortages", json=payload)).json()
    r = await requester_a.post(f"/shortages/{open_one['id']}/confirm")
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    await set_status(session, open_one["id"], Status.CANCELLED)
    r = await requester_a.post(f"/shortages/{open_one['id']}/confirm")
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")


async def test_another_org_cannot_confirm_a_draft(
    requester_a: httpx.AsyncClient,
    client_for: ClientFor,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"], status="DRAFT")
    draft = (await requester_a.post("/shortages", json=payload)).json()
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    r = await manager_b.post(f"/shortages/{draft['id']}/confirm")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    receiver_a = await client_for(world.users["a.RECEIVER"])  # no shortage.create
    assert (await receiver_a.post(f"/shortages/{draft['id']}/confirm")).status_code == 403
    stored = await session.get(Shortage, uuid.UUID(draft["id"]))
    assert stored is not None and stored.status == Status.DRAFT


async def test_the_ai_service_token_cannot_create_or_confirm_a_shortage(
    requester_a: httpx.AsyncClient,
    client_for: ClientFor,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    """Rule 2: even on behalf of a signed-in requester, with a valid chat draft body."""
    draft = (
        await requester_a.post(
            "/shortages",
            json=await chat_body(session, world, products["DIAG-RDK"], status="DRAFT"),
        )
    ).json()
    pair = await auth_service.issue_tokens(session, world.users["a.REQUESTER"])
    ai = await client_for()
    ai.headers["Authorization"] = f"Bearer {settings.ai_service_token}"
    ai.headers["X-On-Behalf-Of"] = pair.access_token
    for status in ("OPEN", "DRAFT"):
        payload = await chat_body(session, world, products["SURG-KIT-A"], status=status)
        r = await ai.post("/shortages", json=payload)
        assert (r.status_code, r.json()["message"]) == (401, REFUSED)
    r = await ai.post(f"/shortages/{draft['id']}/confirm")
    assert (r.status_code, r.json()["message"]) == (401, REFUSED)
    assert await session.scalar(select(func.count()).select_from(Shortage)) == 1
    stored = await session.get(Shortage, uuid.UUID(draft["id"]))
    assert stored is not None and stored.status == Status.DRAFT


# --- cancelling a draft (decision: business-rules §8 DRAFT -> CANCELLED) ---------------------


async def test_the_requester_cancels_a_draft_with_a_reason(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"], status="DRAFT")
    draft = (await requester_a.post("/shortages", json=payload)).json()
    r = await requester_a.post(f"/shortages/{draft['id']}/cancel", json={"reason": "Not needed"})
    assert (r.status_code, r.json()["status"]) == (200, "CANCELLED")
    assert await runs(session, draft["id"]) == 0  # never matched: nothing to release
    rows = await audit_actions(session, draft["id"])
    assert [x.action for x in rows] == ["shortage.created", "shortage.status_changed"]
    cancelled = rows[1]
    assert (cancelled.before, cancelled.after) == ({"status": "DRAFT"}, {"status": "CANCELLED"})
    assert (cancelled.reason, cancelled.reason_source, cancelled.actor_id, cancelled.org_id) == (
        "Not needed", "USER", world.users["a.REQUESTER"].id, world.hospital_a.id,
    )  # fmt: skip


async def test_cancelling_a_draft_without_a_reason_is_system(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["DIAG-RDK"], status="DRAFT")
    draft = (await requester_a.post("/shortages", json=payload)).json()
    r = await requester_a.post(f"/shortages/{draft['id']}/cancel")
    assert r.status_code == 200, r.text
    row = (await audit_actions(session, draft["id"]))[-1]
    assert (row.before, row.after) == ({"status": "DRAFT"}, {"status": "CANCELLED"})
    assert (row.reason, row.reason_source) == ("No reason was entered.", "SYSTEM")


async def test_another_org_cannot_cancel_a_draft(
    requester_a: httpx.AsyncClient,
    client_for: ClientFor,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"], status="DRAFT")
    draft = (await requester_a.post("/shortages", json=payload)).json()
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    r = await manager_b.post(f"/shortages/{draft['id']}/cancel")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    receiver_a = await client_for(world.users["a.RECEIVER"])  # no shortage.create
    assert (await receiver_a.post(f"/shortages/{draft['id']}/cancel")).status_code == 403
    stored = await session.get(Shortage, uuid.UUID(draft["id"]))
    assert stored is not None and stored.status == Status.DRAFT


@pytest.mark.parametrize(
    "status", [Status.IN_FULFILLMENT, Status.RECEIVED, Status.RESOLVED, Status.CANCELLED]
)
async def test_cancel_is_still_409_from_a_state_that_does_not_allow_it(
    requester_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    status: Status,
) -> None:
    payload = await chat_body(session, world, products["SURG-KIT-A"], status="DRAFT")
    draft = (await requester_a.post("/shortages", json=payload)).json()
    await set_status(session, draft["id"], status)
    r = await requester_a.post(f"/shortages/{draft['id']}/cancel")
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    stored = await session.get(Shortage, uuid.UUID(draft["id"]), populate_existing=True)
    assert stored is not None and stored.status == status
