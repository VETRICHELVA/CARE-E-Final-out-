"""S12 reads for the hospital decision screens: a shortage's latest recommendation and its
audit trail. Each covers success, 403 from another org (or without the capability) and 404."""

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.receiving.tests.conftest import receipt
from app.recommendations.tests.conftest import answer_b, open_rec
from app.shipments.models import Shipment
from app.shortages.models import Shortage
from app.source_requests.tests.conftest import Orgs, create_shortage

pytestmark = pytest.mark.anyio


@pytest.fixture
async def approver(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["a.APPROVER"])


async def test_latest_recommendation_is_the_open_one_then_the_last_decided(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    approver: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    url = f"/shortages/{shortage.id}/recommendations/latest"
    r = await approver.get(url)  # B has not answered yet: nothing recommended
    assert (r.status_code, r.json()["code"]) == (404, "not_found")

    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "accept")
    rec = await open_rec(session, shortage)
    r = await approver.get(url)
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["type"], r.json()["status"]) == (
        str(rec.id),
        "TRANSFER",
        "PENDING",
    )
    (line,) = r.json()["lines"]
    assert (line["landed_cost_paise"], line["unit_price_paise"]) == (None, None)  # hospital
    # The requester's own users read it, whatever their role.
    store = await client_for(world.users["a.STORE_MANAGER"])
    assert (await store.get(url)).json()["id"] == str(rec.id)

    # Rejected: matching re-runs (no one declined, so B is asked again) and the rejected
    # recommendation is still the latest until a new one is made.
    r = await approver.post(f"/recommendations/{rec.id}/reject", json={"reason": "Too far"})
    assert r.status_code == 200, r.text
    r = await approver.get(url)
    assert (r.json()["id"], r.json()["status"], r.json()["reason"]) == (
        str(rec.id),
        "REJECTED",
        "Too far",
    )
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "accept")
    newer = await open_rec(session, shortage)
    assert (await approver.get(url)).json()["id"] == str(newer.id)


async def test_latest_recommendation_of_another_orgs_shortage_is_403(
    world: World, shortage: Shortage, client_for: ClientFor
) -> None:
    b = await client_for(world.users["b.APPROVER"])
    r = await b.get(f"/shortages/{shortage.id}/recommendations/latest")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    r = await b.get("/shortages/00000000-0000-0000-0000-000000000000/recommendations/latest")
    assert r.status_code == 404


async def test_the_shortage_trail_shows_every_step_with_user_and_system_reasons(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    s1: Orgs,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
    approver: httpx.AsyncClient,
) -> None:
    """Scenario 1 step 8: A's own trail, oldest first, from creation to the residual."""
    body = receipt(790, 790, reason="60 missing from the pallet.")
    r = await receiver.post(f"/shipments/{po_delivered.id}/receipt", json=body)
    assert r.status_code == 201, r.text
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 10, **({"cursor": cursor} if cursor else {})}
        page = await approver.get(f"/shortages/{shortage.id}/audit", params=params)
        assert page.status_code == 200, page.text
        rows += page.json()["items"]
        if not (cursor := page.json()["next_cursor"]):
            break
    actions = [x["action"] for x in rows]
    assert actions[0] == "shortage.created"
    for action in (
        "match_run.created",
        "source_request.created",
        "source_request.status_changed",  # B's decline, mirrored into A's trail
        "recommendation.created",
        "recommendation.status_changed",
        "purchase_order.created",
        "purchase_order.status_changed",
        "shipment.created",
        "shipment.status_changed",
        "receipt.recorded",
        "inventory_batch.received",
        "reconciliation.completed",
    ):
        assert action in actions, action
    assert [x["ts"] for x in rows] == sorted(x["ts"] for x in rows)
    assert all(x["org_id"] == str(world.hospital_a.id) for x in rows)
    recorded = next(x for x in rows if x["action"] == "receipt.recorded")
    assert (recorded["reason"], recorded["reason_source"]) == (
        "60 missing from the pallet.",
        "USER",
    )
    reconciled = next(x for x in rows if x["action"] == "reconciliation.completed")
    assert (reconciled["reason_source"], reconciled["actor_id"]) == ("SYSTEM", None)
    # Holds and B's stock rows stay in B's org; they never appear in A's trail.
    assert not any(str(x["entity"]) in ("hold", "inventory_batch") and x["action"] != (
        "inventory_batch.received"
    ) for x in rows)  # fmt: skip


async def test_the_trail_needs_audit_read_and_the_own_org(
    world: World,
    shortage: Shortage,
    products: dict[str, Product],
    client_for: ClientFor,
    session: AsyncSession,
) -> None:
    url = f"/shortages/{shortage.id}/audit"
    store = await client_for(world.users["a.STORE_MANAGER"])
    r = await store.get(url)
    assert (r.status_code, r.json()["details"]) == (403, {"capability": "audit.read"})
    r = await (await client_for(world.users["b.APPROVER"])).get(url)
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    r = await (await client_for(world.users["p.ADMIN"])).get(url)
    assert r.status_code == 403  # the platform admin reads every org's rows via GET /audit
    # B's own shortage trail does not include A's rows.
    own = await create_shortage(
        session, world.users["b.STORE_MANAGER"], products["SURG-KIT-A"], shortage.created_at
    )
    r = await (await client_for(world.users["b.APPROVER"])).get(f"/shortages/{own.id}/audit")
    assert r.status_code == 200
    assert {x["org_id"] for x in r.json()["items"]} == {str(world.hospital_b.id)}
