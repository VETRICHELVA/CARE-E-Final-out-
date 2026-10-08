"""S12 fixtures: Scenario 1 (S06/S09/S11 fixtures) up to a DELIVERED shipment, either the
TRANSFER of 850 from Hospital B or the BUY of 850 from Supplier Y (steps 3-6)."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conftest import ClientFor, World
from app.purchase_orders.models import PurchaseOrder
from app.recommendations.tests.conftest import answer_b, open_rec
from app.shipments.models import Shipment
from app.shipments.tests import conftest as s11
from app.shipments.tests.conftest import Fleet, assign, step
from app.shortages.models import Shortage
from app.source_requests.tests.conftest import Orgs, add_user

# Earlier sections' fixtures, shared here.
now = s11.now
s1 = s11.s1
shortage = s11.shortage
swiftmed = s11.swiftmed
other_fleet = s11.other_fleet
shipment = s11.shipment
dispatcher = s11.dispatcher
ravi = s11.ravi


async def deliver(dispatcher: httpx.AsyncClient, ravi: httpx.AsyncClient, fleet: Fleet,
                  shipment: Shipment) -> None:  # fmt: skip
    """Assign to Ravi, then pick up, travel and deliver (S11)."""
    r = await assign(dispatcher, shipment, fleet)
    assert r.status_code == 200, r.text
    for status in ("PICKED_UP", "IN_TRANSIT", "DELIVERED"):
        r = await step(ravi, shipment, status)
        assert r.status_code == 200, r.text


@pytest.fixture
async def delivered(
    session: AsyncSession,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    swiftmed: Fleet,
) -> Shipment:
    """The TRANSFER of 850 from Hospital B, DELIVERED to Hospital A."""
    await deliver(dispatcher, ravi, swiftmed, shipment)
    await session.refresh(shipment)
    return shipment


@pytest.fixture
async def desk_y(session: AsyncSession, s1: Orgs, client_for: ClientFor) -> httpx.AsyncClient:
    return await client_for(await add_user(session, s1["Supplier Y"], "SUPPLIER_DESK", "d@y.test"))


async def buy_from_y(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    client_for: ClientFor,
    desk_y: httpx.AsyncClient,
) -> PurchaseOrder:
    """Scenario 1 steps 3-6 up to dispatch: B declines, "Approve purchase" from Supplier Y,
    which acknowledges and dispatches."""
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "decline")
    rec = await open_rec(session, shortage)
    r = await (await client_for(world.users["a.APPROVER"])).post(
        f"/recommendations/{rec.id}/approve", json={}
    )
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "The order has gone to the supplier."
    po = await session.get_one(PurchaseOrder, r.json()["purchase_order_id"])
    for action in ("acknowledge", "dispatch"):
        r = await desk_y.post(f"/purchase-orders/{po.id}/{action}", json={})
        assert r.status_code == 200, r.text
    return po


@pytest.fixture
async def po_delivered(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    client_for: ClientFor,
    desk_y: httpx.AsyncClient,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
) -> Shipment:
    """Scenario 1 step 6: Supplier Y's 850 DELIVERED to Hospital A."""
    po = await buy_from_y(session, world, shortage, client_for, desk_y)
    shipment = await session.scalar(select(Shipment).where(Shipment.purchase_order_id == po.id))
    assert shipment is not None
    await deliver(dispatcher, ravi, swiftmed, shipment)
    await session.refresh(shipment)
    return shipment


@pytest.fixture
async def receiver(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["a.RECEIVER"])


def receipt(
    received: int, accepted: int, rejected: int = 0, *, condition: str = "GOOD", **extra: object
) -> dict[str, object]:
    body: dict[str, object] = {
        "received": received,
        "accepted": accepted,
        "rejected": rejected,
        "condition": condition,
        **extra,
    }
    if accepted and "expiry_date" not in body:
        body["expiry_date"] = "2027-04-30"
    return body
