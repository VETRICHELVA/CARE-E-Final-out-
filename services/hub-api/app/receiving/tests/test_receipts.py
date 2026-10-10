"""S12 receipts and reconciliation (business-rules.md §8, §9, §10): Scenario 1 end to end
through the API, full acceptance, the invariants, the cold-chain inspection note, splits,
the new batch, audit rows and events. Each endpoint covers success, 403 from another org,
and 409 on the shipment state machine."""

from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.inventory.models import InventoryBatch
from app.orgs.models import OrgType
from app.purchase_orders.models import PurchaseOrder
from app.receiving.models import Receipt, Reconciliation
from app.receiving.tests.conftest import deliver, receipt
from app.recommendations.tests.conftest import add_hospitals, audit_of, open_rec, outbox
from app.shipments import service as shipments
from app.shipments.models import Shipment
from app.shipments.tests.conftest import Fleet, step
from app.shortages.models import MatchRun, Shortage
from app.source_requests.tests.conftest import (
    Orgs,
    add_org,
    add_user,
    create_shortage,
    requests_of,
)

pytestmark = pytest.mark.anyio


async def fresh(session: AsyncSession, model: type, obj_id: object) -> object:
    return await session.get_one(model, obj_id, populate_existing=True)


async def children_of(session: AsyncSession, parent: Shortage) -> list[Shortage]:
    stmt = select(Shortage).where(Shortage.parent_shortage_id == parent.id)
    return list(await session.scalars(stmt))


async def batches_of_a(session: AsyncSession, world: World, product_id: object) -> list[str]:
    stmt = select(InventoryBatch.batch_no).where(
        InventoryBatch.org_id == world.hospital_a.id, InventoryBatch.product_id == product_id
    )
    return sorted(await session.scalars(stmt))


# --- Scenario 1 ---------------------------------------------------------------------------------


async def test_scenario_1_short_delivery_opens_a_residual_of_60_that_is_matching(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    s1: Orgs,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    """Scenario 1 step 7: Hospital A records received 790, accepted 790, rejected 0."""
    r = await receiver.post(
        f"/shipments/{po_delivered.id}/receipt",
        json=receipt(790, 790, expiry_date="2027-03-31", batch_no="Y-0042"),
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["expected"], out["received"], out["accepted"], out["rejected"]) == (
        850,
        790,
        790,
        0,
    )
    assert (out["condition"], out["received_by"]) == ("GOOD", str(world.users["a.RECEIVER"].id))
    rec = out["reconciliation"]
    assert (rec["expected"], rec["accepted"], rec["discrepancy"], rec["outcome"]) == (
        850,
        790,
        60,
        "PARTIAL",
    )

    parent = await fresh(session, Shortage, shortage.id)
    assert isinstance(parent, Shortage) and parent.status == "PARTIALLY_RESOLVED"
    (residual,) = await children_of(session, shortage)
    assert rec["residual_shortage_id"] == str(residual.id)
    assert (residual.qty_required, residual.qty_local_usable, residual.shortfall) == (60, 0, 60)
    assert (residual.product_id, residual.priority, residual.min_shelf_life_days) == (
        shortage.product_id,
        shortage.priority,
        shortage.min_shelf_life_days,
    )
    assert (residual.org_id, residual.facility_id) == (shortage.org_id, shortage.facility_id)
    assert residual.status == "MATCHING"
    runs = list(await session.scalars(select(MatchRun).where(MatchRun.shortage_id == residual.id)))
    assert [(run.run_no, run.triggered_by) for run in runs] == [(1, "CREATE")]

    shipment = await fresh(session, Shipment, po_delivered.id)
    assert isinstance(shipment, Shipment) and shipment.status == "RECONCILED"
    po = await fresh(session, PurchaseOrder, shipment.purchase_order_id)
    assert isinstance(po, PurchaseOrder) and po.status == "DELIVERED"

    # The accepted stock is a new batch in A's inventory, at the purchase price.
    batch = await session.get_one(InventoryBatch, out["batch_id"])
    assert (batch.org_id, batch.facility_id, batch.batch_no, batch.on_hand) == (
        world.hospital_a.id,
        shortage.facility_id,
        "Y-0042",
        790,
    )
    assert (batch.expiry_date, batch.unit_cost_paise, batch.last_verified_at) == (
        date(2027, 3, 31),
        2800,
        None,
    )

    # The requester's views: the residual is listed and A's approver can read it.
    approver = await client_for(world.users["a.APPROVER"])
    r = await approver.get(f"/shortages/{residual.id}")
    assert r.status_code == 200, r.text
    assert (r.json()["parent_shortage_id"], r.json()["shortfall"]) == (str(shortage.id), 60)

    (event,) = await outbox(session, EventType.RECONCILIATION_COMPLETED)
    assert event["org_ids"] == [str(world.hospital_a.id)]
    assert event["data"] == {
        "shortage_id": str(shortage.id),
        "outcome": "PARTIAL",
        "residual_shortage_id": str(residual.id),
    }


async def test_a_residual_inherits_the_parents_excluded_sources(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    s1: Orgs,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    """§9: Hospital B declined the parent (Scenario 1 step 3), so the residual of 60 does
    not ask B again; its explanation says why B was left out."""
    other = await client_for(world.users["b.STORE_MANAGER"])
    body = receipt(790, 790, expiry_date="2027-03-31")
    assert (await other.post(f"/shipments/{po_delivered.id}/receipt", json=body)).status_code == 403
    r = await receiver.post(f"/shipments/{po_delivered.id}/receipt", json=body)
    assert r.status_code == 201, r.text
    again = await receiver.post(f"/shipments/{po_delivered.id}/receipt", json=body)
    assert again.status_code == 409

    b, c = s1["Hospital B"], s1["Hospital C"]
    (residual,) = await children_of(session, shortage)
    (run,) = await session.scalars(select(MatchRun).where(MatchRun.shortage_id == residual.id))
    assert run.excluded_org_ids == [b.id]
    (sr,) = await requests_of(session, residual)
    assert (sr.source_org_id, sr.qty) == (c.id, 60)  # C's 100 transferable covers 60

    manager_c = await client_for(await add_user(session, c, "STORE_MANAGER", "m@c.test"))
    r = await manager_c.post(f"/source-requests/{sr.id}/accept", json={})
    assert r.status_code == 200, r.text
    rec = await open_rec(session, residual)
    assert "Hospital B (declined the request for the earlier shortage)" in rec.explanation


async def test_scenario_1_audit_rows(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    s1: Orgs,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    r = await receiver.post(f"/shipments/{po_delivered.id}/receipt", json=receipt(790, 790))
    assert r.status_code == 201, r.text
    out = r.json()
    a, y = world.hospital_a.id, s1["Supplier Y"].id
    me = world.users["a.RECEIVER"].id

    (row,) = await audit_of(session, out["id"])
    assert (row.action, row.org_id, row.actor_id) == ("receipt.recorded", a, me)
    assert (row.reason, row.reason_source) == ("No reason was entered.", "SYSTEM")
    assert row.after is not None and row.after["accepted"] == 790

    (row,) = await audit_of(session, out["batch_id"])
    assert (row.action, row.org_id, row.actor_id) == ("inventory_batch.received", a, me)

    moves = [x for x in await audit_of(session, po_delivered.id) if x.action.endswith("changed")]
    assert (moves[-1].after, moves[-1].actor_id, moves[-1].org_id) == (
        {"status": "RECONCILED", "receipt_id": out["id"]},
        me,
        a,
    )

    po_rows = await audit_of(session, po_delivered.purchase_order_id)  # type: ignore[arg-type]
    delivered = [x for x in po_rows if x.after == {"status": "DELIVERED"}]
    assert sorted((x.org_id, x.actor_id, x.reason_source) for x in delivered) == sorted(
        [(a, None, "SYSTEM"), (y, None, "SYSTEM")]
    )
    assert {x.reason for x in delivered} == {
        "The buyer recorded the receipt of this order's shipment."
    }

    statuses = [
        (x.after["status"], x.actor_id, x.reason_source, x.reason)  # type: ignore[index]
        for x in await audit_of(session, shortage.id)
        if x.action == "shortage.status_changed"
    ]
    assert statuses[-2:] == [
        ("RECEIVED", me, "SYSTEM", "No reason was entered."),
        (
            "PARTIALLY_RESOLVED",
            None,
            "SYSTEM",
            "Accepted 790 of the 850 short; a residual shortage of 60 was opened.",
        ),
    ]
    (residual,) = await children_of(session, shortage)
    created = (await audit_of(session, residual.id))[0]
    assert (created.action, created.actor_id, created.reason_source, created.reason) == (
        "shortage.created",
        None,
        "SYSTEM",
        "Opened for the 60 not accepted of the parent shortage's 850.",
    )
    recon = await session.scalar(
        select(Reconciliation).where(Reconciliation.shipment_id == po_delivered.id)
    )
    assert recon is not None
    (row,) = await audit_of(session, recon.id)
    assert (row.action, row.org_id, row.actor_id) == ("reconciliation.completed", a, None)
    # Nothing about the receipt reaches Supplier Y's own trail but its order's state.
    y_rows = await session.scalars(select(AuditLog).where(AuditLog.org_id == y))
    assert all(x.entity in ("purchase_order", "shipment") for x in y_rows)


async def test_a_typed_reason_is_the_users(
    session: AsyncSession, delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    body = receipt(850, 850, reason="Counted twice at the dock.")
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert r.status_code == 201, r.text
    (row,) = await audit_of(session, r.json()["id"])
    assert (row.reason, row.reason_source) == ("Counted twice at the dock.", "USER")


# --- full acceptance, transfers and splits --------------------------------------------------


async def test_accepting_the_full_quantity_resolves(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    """The TRANSFER of 850 from Hospital B, accepted in full."""
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=receipt(850, 850))
    assert r.status_code == 201, r.text
    rec = r.json()["reconciliation"]
    assert (rec["discrepancy"], rec["outcome"], rec["residual_shortage_id"]) == (
        0,
        "CONFIRMED",
        None,
    )
    parent = await fresh(session, Shortage, shortage.id)
    assert isinstance(parent, Shortage) and parent.status == "RESOLVED"
    assert await children_of(session, shortage) == []
    # A transfer's batch: the source's cost is never copied to the receiver.
    batch = await session.get_one(InventoryBatch, r.json()["batch_id"])
    assert (batch.batch_no, batch.on_hand, batch.unit_cost_paise) == (
        f"RCV-{delivered.id.hex[:8].upper()}",
        850,
        0,
    )
    (event,) = await outbox(session, EventType.RECONCILIATION_COMPLETED)
    assert event["data"]["outcome"] == "CONFIRMED"
    assert event["data"]["residual_shortage_id"] is None


async def test_rejecting_everything_adds_no_batch_and_reopens_the_whole_shortfall(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    before = await batches_of_a(session, world, shortage.product_id)
    body = receipt(850, 0, 850, condition="DAMAGED", inspection_note="Crushed in transit.")
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert r.status_code == 201, r.text
    assert (r.json()["batch_id"], r.json()["inspection_note"]) == (None, "Crushed in transit.")
    assert await batches_of_a(session, world, shortage.product_id) == before
    (residual,) = await children_of(session, shortage)
    assert residual.shortfall == 850
    assert await outbox(session, EventType.INVENTORY_CHANGED) != []  # B's pickup, not A's
    assert all(
        e["org_ids"] != [str(world.hospital_a.id)]
        for e in await outbox(session, EventType.INVENTORY_CHANGED)
    )


async def test_a_split_reconciles_once_every_shipment_has_a_receipt(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    now: datetime,
    client_for: ClientFor,
    receiver: httpx.AsyncClient,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    swiftmed: Fleet,
) -> None:
    ska = products["SURG-KIT-A"]
    sources = await add_hospitals(session, ska, now, [500, 400])
    split = await create_shortage(session, world.users["a.REQUESTER"], ska, now)
    for sr in await requests_of(session, split):
        org = next(o for o in sources.values() if o.id == sr.source_org_id)
        manager = await add_user(session, org, "STORE_MANAGER", f"m@{org.name[-2:]}.test")
        r = await (await client_for(manager)).post(f"/source-requests/{sr.id}/accept", json={})
        assert r.status_code == 200, r.text
    rec = await open_rec(session, split)
    assert rec.type == "TRANSFER_SPLIT"
    r = await (await client_for(world.users["a.APPROVER"])).post(
        f"/recommendations/{rec.id}/approve", json={}
    )
    assert r.status_code == 200, r.text
    first, second = [await session.get_one(Shipment, i) for i in r.json()["shipment_ids"]]
    for s in (first, second):
        await deliver(dispatcher, ravi, swiftmed, s)

    r = await receiver.post(
        f"/shipments/{first.id}/receipt", json=receipt(first.qty, first.qty, batch_no="S-1")
    )
    assert r.status_code == 201, r.text
    assert r.json()["reconciliation"] is None
    still = await fresh(session, Shortage, split.id)
    assert isinstance(still, Shortage) and still.status == "IN_FULFILLMENT"
    assert await outbox(session, EventType.RECONCILIATION_COMPLETED) == []

    r = await receiver.post(
        f"/shipments/{second.id}/receipt",
        json=receipt(second.qty, second.qty - 10, 10, batch_no="S-2"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["reconciliation"]["discrepancy"] == 10
    done = await fresh(session, Shortage, split.id)
    assert isinstance(done, Shortage) and done.status == "PARTIALLY_RESOLVED"
    rows = list(
        await session.scalars(select(Reconciliation).where(Reconciliation.shortage_id == split.id))
    )
    (residual,) = await children_of(session, split)
    assert sorted((x.shipment_id, x.discrepancy) for x in rows) == sorted(
        [(first.id, 0), (second.id, 10)]
    )
    assert {(x.outcome, x.residual_shortage_id) for x in rows} == {("PARTIAL", residual.id)}
    assert residual.shortfall == 10
    assert len(await outbox(session, EventType.RECONCILIATION_COMPLETED)) == 1
    # The first receipt's view now shows the reconciliation too.
    r = await receiver.get(f"/shipments/{first.id}")
    assert r.json()["receipt"]["reconciliation"]["outcome"] == "PARTIAL"


# --- invariants and validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (receipt(790, 780, 0), "Accepted 780 plus rejected 0 must equal received 790."),
        (receipt(790, 790, 10), "Accepted 790 plus rejected 10 must equal received 790."),
        (receipt(851, 851, 0), "Received 851 is more than the 850 expected."),
    ],
)
async def test_figures_breaking_the_invariants_are_400(
    session: AsyncSession,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
    body: dict[str, object],
    message: str,
) -> None:
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert (r.status_code, r.json()["code"], r.json()["message"]) == (400, "validation", message)
    assert r.json()["details"]["expected"] == 850
    shipment = await fresh(session, Shipment, delivered.id)
    assert isinstance(shipment, Shipment) and shipment.status == "DELIVERED"
    assert await session.scalar(select(Receipt).where(Receipt.shipment_id == delivered.id)) is None


async def test_expected_comes_from_the_shipment_not_the_client(
    delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    body = {**receipt(850, 850), "expected": 900}
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert (r.status_code, r.json()["code"]) == (422, "schema_error")


async def test_negative_figures_and_unknown_conditions_are_422(
    delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    for body in (receipt(-1, 0, 0), receipt(850, 850, condition="WET")):
        r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
        assert r.status_code == 422, r.text


async def test_accepted_stock_needs_an_expiry_date(
    delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    body = {"received": 850, "accepted": 850, "rejected": 0, "condition": "GOOD"}
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert (r.status_code, r.json()["details"]) == (400, {"reason": "expiry_date_required"})


async def test_a_batch_number_already_used_is_409_conflict(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    (existing,) = await batches_of_a(session, world, shortage.product_id)
    r = await receiver.post(
        f"/shipments/{delivered.id}/receipt", json=receipt(850, 850, batch_no=existing)
    )
    assert (r.status_code, r.json()["code"]) == (409, "conflict")
    shipment = await fresh(session, Shipment, delivered.id)
    assert isinstance(shipment, Shipment) and shipment.status == "DELIVERED"


async def test_an_open_excursion_needs_an_inspection_note(
    delivered: Shipment, receiver: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def excursion(*_: object) -> bool:
        return True

    monkeypatch.setattr(shipments, "has_open_excursion", excursion)
    assert (await receiver.get(f"/shipments/{delivered.id}")).json()[
        "inspection_note_required"
    ] is True
    for note in (None, "   "):
        body = receipt(850, 850, condition="TEMPERATURE_ISSUE", inspection_note=note)
        r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
        assert (r.status_code, r.json()["details"]) == (
            400,
            {"reason": "inspection_note_required"},
        )
    body = receipt(850, 850, inspection_note="Probe 4.1 °C on arrival; seals intact.")
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=body)
    assert r.status_code == 201, r.text


async def test_without_an_excursion_no_note_is_needed(
    session: AsyncSession, delivered: Shipment, receiver: httpx.AsyncClient
) -> None:
    assert await shipments.has_open_excursion(session, delivered) is False
    r = await receiver.get(f"/shipments/{delivered.id}")
    assert (r.json()["inspection_note_required"], r.json()["receipt"]) == (False, None)


# --- who may record, and when ---------------------------------------------------------------


async def test_only_the_receiving_org_with_the_capability_records_a_receipt(
    session: AsyncSession,
    world: World,
    s1: Orgs,
    delivered: Shipment,
    client_for: ClientFor,
    swiftmed: Fleet,
) -> None:
    b_receiver = await add_user(session, world.hospital_b, "RECEIVER", "rcv@b.test")
    c_receiver = await add_user(session, s1["Hospital C"], "RECEIVER", "rcv@c.test")
    carrier = await add_user(session, swiftmed.org, "ADMIN", "admin@swiftmed.test")
    for user in (b_receiver, c_receiver, carrier):  # source, uninvolved, carrier
        r = await (await client_for(user)).post(
            f"/shipments/{delivered.id}/receipt", json=receipt(850, 850)
        )
        assert (r.status_code, r.json()["code"]) == (403, "forbidden"), user.email
    # A's store manager belongs to the receiving org but cannot record receipts.
    r = await (await client_for(world.users["a.STORE_MANAGER"])).post(
        f"/shipments/{delivered.id}/receipt", json=receipt(850, 850)
    )
    assert (r.status_code, r.json()["details"]) == (403, {"capability": "receipt.record"})


async def test_a_receipt_before_delivery_or_twice_is_409(
    session: AsyncSession,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    swiftmed: Fleet,
    receiver: httpx.AsyncClient,
) -> None:
    url = f"/shipments/{shipment.id}/receipt"
    r = await receiver.post(url, json=receipt(850, 850))
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    r = await dispatcher.post(
        f"/shipments/{shipment.id}/assign",
        json={"driver_id": str(swiftmed.drivers["Ravi"].id), "vehicle_id": str(swiftmed.van.id)},
    )
    assert r.status_code == 200
    for status in ("PICKED_UP", "IN_TRANSIT"):
        assert (await step(ravi, shipment, status)).status_code == 200
        r = await receiver.post(url, json=receipt(850, 850))
        assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    assert (await step(ravi, shipment, "DELIVERED")).status_code == 200
    assert (await receiver.post(url, json=receipt(850, 850))).status_code == 201
    r = await receiver.post(url, json=receipt(850, 850, batch_no="AGAIN"))
    assert (r.status_code, r.json()["code"]) == (409, "invalid_transition")
    assert r.json()["message"] == "Cannot move from RECONCILED to RECONCILED."


async def test_unknown_shipment_is_404(receiver: httpx.AsyncClient) -> None:
    r = await receiver.post(
        "/shipments/00000000-0000-0000-0000-000000000000/receipt", json=receipt(1, 1)
    )
    assert r.status_code == 404


# --- reads ----------------------------------------------------------------------------------


async def test_only_the_receiving_org_sees_the_receipt_on_the_shipment(
    session: AsyncSession,
    world: World,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
    client_for: ClientFor,
    dispatcher: httpx.AsyncClient,
) -> None:
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=receipt(850, 850))
    assert r.status_code == 201
    mine = (await receiver.get(f"/shipments/{delivered.id}")).json()
    assert (mine["status"], mine["receipt"]["id"]) == ("RECONCILED", r.json()["id"])
    assert mine["receipt"]["reconciliation"]["outcome"] == "CONFIRMED"
    source = await client_for(world.users["b.STORE_MANAGER"])
    for client in (source, dispatcher):
        other = (await client.get(f"/shipments/{delivered.id}")).json()
        assert (other["status"], other["receipt"]) == ("RECONCILED", None)


async def test_inbound_and_outbound_shipments(
    world: World, delivered: Shipment, receiver: httpx.AsyncClient, client_for: ClientFor
) -> None:
    def listed(r: httpx.Response) -> list[str]:
        assert r.status_code == 200, r.text
        return [s["id"] for s in r.json()["items"]]

    assert listed(await receiver.get("/shipments", params={"direction": "inbound"})) == [
        str(delivered.id)
    ]
    assert listed(await receiver.get("/shipments", params={"direction": "outbound"})) == []
    source = await client_for(world.users["b.STORE_MANAGER"])
    assert listed(await source.get("/shipments", params={"direction": "outbound"})) == [
        str(delivered.id)
    ]
    assert listed(await source.get("/shipments", params={"direction": "inbound"})) == []
    r = await receiver.get("/shipments", params={"direction": "sideways"})
    assert r.status_code == 422


async def test_an_uninvolved_receiver_cannot_read_the_shipment(
    session: AsyncSession, delivered: Shipment, client_for: ClientFor
) -> None:
    other = await add_org(session, "Hospital Q", OrgType.HOSPITAL, 12.9, 77.5)
    user = await add_user(session, other, "RECEIVER", "rcv@q.test")
    r = await (await client_for(user)).get(f"/shipments/{delivered.id}")
    assert r.status_code == 403
