"""Purchase orders on the supplier side: list, acknowledge, dispatch (creates the shipment)
and reject (re-runs matching without that supplier); success, 403 from another org or
without the capability, and 409 on the state machine."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.purchase_orders.models import PurchaseOrder
from app.recommendations.tests.conftest import (
    answer_b,
    audit_of,
    open_rec,
    outbox,
    recs_of,
    runs_of,
)
from app.shipments.models import Shipment
from app.shortages.models import Shortage
from app.source_requests.tests.conftest import Orgs, add_user

pytestmark = pytest.mark.anyio


@pytest.fixture
async def po(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    client_for: ClientFor,
) -> PurchaseOrder:
    """Scenario 1 steps 3-5: B declines, the BUY from Supplier Y is approved."""
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "decline")
    rec = await open_rec(session, shortage)
    approver = await client_for(world.users["a.APPROVER"])
    r = await approver.post(f"/recommendations/{rec.id}/approve", json={})
    assert r.status_code == 200, r.text
    return await session.get_one(PurchaseOrder, r.json()["purchase_order_id"])


@pytest.fixture
async def desk_y(session: AsyncSession, s1: Orgs, client_for: ClientFor) -> httpx.AsyncClient:
    return await client_for(await add_user(session, s1["Supplier Y"], "SUPPLIER_DESK", "d@y.test"))


@pytest.fixture
async def desk_x(session: AsyncSession, s1: Orgs, client_for: ClientFor) -> httpx.AsyncClient:
    return await client_for(await add_user(session, s1["Supplier X"], "SUPPLIER_DESK", "d@x.test"))


async def test_the_supplier_lists_only_its_own_orders(
    world: World,
    po: PurchaseOrder,
    desk_y: httpx.AsyncClient,
    desk_x: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    r = await desk_y.get("/purchase-orders")
    assert r.status_code == 200, r.text
    (item,) = r.json()["items"]
    assert (item["id"], item["status"], item["qty"], item["unit_price_paise"]) == (
        str(po.id),
        "SENT",
        850,
        2800,
    )
    assert (item["to_org_id"], item["to_org_name"]) == (str(world.hospital_a.id), "Hospital A")
    assert item["shipment_id"] is None
    assert (await desk_y.get("/purchase-orders?status=DISPATCHED")).json()["items"] == []
    assert (await desk_x.get("/purchase-orders")).json()["items"] == []
    # A hospital user has no `po.respond`; a hospital admin has it but is not a supplier.
    for key in ("a.STORE_MANAGER", "a.ADMIN"):
        r = await (await client_for(world.users[key])).get("/purchase-orders")
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_acknowledge_then_dispatch_creates_the_shipment(
    session: AsyncSession,
    world: World,
    s1: Orgs,
    shortage: Shortage,
    po: PurchaseOrder,
    desk_y: httpx.AsyncClient,
) -> None:
    r = await desk_y.post(f"/purchase-orders/{po.id}/acknowledge", json={})
    assert (r.status_code, r.json()["status"]) == (200, "ACKNOWLEDGED")
    r = await desk_y.post(f"/purchase-orders/{po.id}/dispatch", json={"reason": "Van 4"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "DISPATCHED"
    shipment = await session.get_one(Shipment, body["shipment_id"])
    y = s1["Supplier Y"]
    assert (shipment.purchase_order_id, shipment.source_request_id) == (po.id, None)
    assert (shipment.from_org_id, shipment.to_org_id) == (y.id, world.hospital_a.id)
    assert (shipment.qty, shipment.status, shipment.planned_eta) == (850, "CREATED", po.eta)
    await session.refresh(shortage)
    assert shortage.status == "IN_FULFILLMENT"
    changes = await outbox(session, EventType.PURCHASE_ORDER_STATUS_CHANGED)
    assert [(e["data"]["from"], e["data"]["to"]) for e in changes] == [
        ("SENT", "ACKNOWLEDGED"),
        ("ACKNOWLEDGED", "DISPATCHED"),
    ]
    assert all(
        sorted(e["org_ids"]) == sorted([str(world.hospital_a.id), str(y.id)]) for e in changes
    )
    (shipped,) = await outbox(session, EventType.SHIPMENT_CREATED)
    assert shipped["data"]["shipment_id"] == str(shipment.id)
    created = await audit_of(session, shipment.id)
    assert {(r_.org_id, r_.actor_id is None) for r_ in created} == {
        (po.supplier_org_id, False),  # the supplier dispatched, in its own trail
        (shipment.to_org_id, True),  # mirrored to the hospital without the user's id
    }
    rows = await audit_of(session, po.id)
    supplier = [r_ for r_ in rows if r_.org_id == po.supplier_org_id]
    hospital = [r_ for r_ in rows if r_.org_id != po.supplier_org_id]
    # The supplier's trail has its own actions; the hospital's has the order and mirrors of
    # the supplier's actions without the supplier user's id.
    assert [r_.after["status"] for r_ in supplier] == ["ACKNOWLEDGED", "DISPATCHED"]  # type: ignore[index]
    assert [r_.after["status"] for r_ in hospital] == ["SENT", "ACKNOWLEDGED", "DISPATCHED"]  # type: ignore[index]
    assert all(r_.actor_id is not None for r_ in supplier)
    assert [r_.actor_id for r_ in hospital[1:]] == [None, None]
    for trail in (supplier, hospital):
        assert (trail[-1].reason, trail[-1].reason_source) == ("Van 4", "USER")
        assert (trail[-2].reason, trail[-2].reason_source) == ("No reason was entered.", "SYSTEM")


async def test_another_supplier_cannot_touch_the_order(
    session: AsyncSession, po: PurchaseOrder, desk_x: httpx.AsyncClient
) -> None:
    for action in ("acknowledge", "dispatch", "reject"):
        r = await desk_x.post(f"/purchase-orders/{po.id}/{action}", json={})
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    await session.refresh(po)
    assert po.status == "SENT"


@pytest.mark.parametrize(
    ("steps", "action"),
    [
        ([], "dispatch"),  # SENT must be acknowledged first
        (["acknowledge"], "acknowledge"),
        (["acknowledge", "dispatch"], "reject"),
        (["acknowledge", "dispatch"], "dispatch"),
        (["reject"], "acknowledge"),
        (["reject"], "reject"),
    ],
)
async def test_transitions_outside_the_state_machine_are_409(
    po: PurchaseOrder, desk_y: httpx.AsyncClient, steps: list[str], action: str
) -> None:
    for step in steps:
        assert (await desk_y.post(f"/purchase-orders/{po.id}/{step}")).status_code == 200
    r = await desk_y.post(f"/purchase-orders/{po.id}/{action}", json={})
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")


@pytest.mark.parametrize("acknowledged", [False, True])
async def test_a_rejection_rematches_without_that_supplier(
    session: AsyncSession,
    world: World,
    s1: Orgs,
    shortage: Shortage,
    po: PurchaseOrder,
    desk_y: httpx.AsyncClient,
    acknowledged: bool,
) -> None:
    if acknowledged:
        assert (await desk_y.post(f"/purchase-orders/{po.id}/acknowledge")).status_code == 200
    r = await desk_y.post(f"/purchase-orders/{po.id}/reject", json={"reason": "Out of stock"})
    assert (r.status_code, r.json()["status"]) == (200, "REJECTED")
    x, y, b = s1["Supplier X"], s1["Supplier Y"], world.hospital_b
    run = (await runs_of(session, shortage))[-1]
    assert (run.triggered_by, sorted(run.excluded_org_ids)) == ("DECLINE", sorted([b.id, y.id]))
    # The shortage went IN_FULFILLMENT -> MATCHING, and the re-run's BUY from X awaits a decision.
    statuses = [e["data"] for e in await outbox(session, EventType.SHORTAGE_STATUS_CHANGED)]
    assert {"shortage_id": str(shortage.id), "from": "IN_FULFILLMENT", "to": "MATCHING"} in (
        statuses
    )
    await session.refresh(shortage)
    assert shortage.status == "AWAITING_DECISION"
    rec = await open_rec(session, shortage)
    assert (rec.type, [x_["source_org_id"] for x_ in rec.lines]) == ("BUY", [str(x.id)])
    assert rec.alternatives == []
    assert "Supplier Y (rejected the purchase order)" in rec.explanation
    assert len(await recs_of(session, shortage)) == 2
    rejected = [
        r for r in await audit_of(session, po.id) if (r.after or {}).get("status") == "REJECTED"
    ]
    me = (await desk_y.get("/auth/me")).json()["user"]["id"]
    # Y's own trail has its reject with its user; Hospital A's a mirror without Y's user.
    assert {
        (str(r.org_id), str(r.actor_id) if r.actor_id else None, r.reason, r.reason_source)
        for r in rejected
    } == {
        (str(y.id), me, "Out of stock", "USER"),
        (str(shortage.org_id), None, "Out of stock", "USER"),
    }
    assert not await session.scalar(select(Shipment.id).where(Shipment.purchase_order_id == po.id))
