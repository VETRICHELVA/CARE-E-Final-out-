"""S11 shipment endpoints: the dispatch board, assign/unassign (route, ETA, cold-chain
vehicle rule), the assigned driver's status steps with the pickup draw-down (§9), location
pings, drivers and vehicles. Each covers success, 403 from another org, and 409 on the §8
state machine."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.costing import HaversineProvider, Point, transport_eta_hours
from app.domain.events import EventType
from app.inventory.models import InventoryBatch
from app.orgs.models import Facility
from app.recommendations.tests.conftest import audit_of, outbox
from app.shipments.models import LocationPing, Shipment, ShipmentLeg
from app.shipments.tests.conftest import Fleet, approve_transfer, assign, step
from app.shortages.models import Shortage
from app.source_requests.models import Hold
from app.source_requests.tests.conftest import Orgs

pytestmark = pytest.mark.anyio

B_STORE, A_STORE = Point(12.93, 77.62), Point(12.97, 77.59)  # the World facilities


def ids(*orgs: object) -> list[str]:
    return sorted(str(getattr(o, "id", o)) for o in orgs)


async def holds_of(session: AsyncSession, shipment: Shipment) -> list[Hold]:
    stmt = select(Hold).where(Hold.source_request_id == shipment.source_request_id)
    return list(await session.scalars(stmt.execution_options(populate_existing=True)))


async def batch_of_b(session: AsyncSession, world: World) -> InventoryBatch:
    batch = await session.scalar(
        select(InventoryBatch)
        .where(InventoryBatch.org_id == world.hospital_b.id)
        .execution_options(populate_existing=True)
    )
    assert batch is not None
    return batch


# --- the dispatch board and reads -------------------------------------------------------------


async def test_every_involved_org_and_every_logistics_org_sees_an_unassigned_shipment(
    world: World,
    shipment: Shipment,
    client_for: ClientFor,
    dispatcher: httpx.AsyncClient,
    other_dispatcher: httpx.AsyncClient,
) -> None:
    for client in (dispatcher, other_dispatcher):
        r = await client.get("/shipments", params={"status": "CREATED"})
        assert r.status_code == 200, r.text
        assert [s["id"] for s in r.json()["items"]] == [str(shipment.id)]
    r = await (await client_for(world.users["a.REQUESTER"])).get(f"/shipments/{shipment.id}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["from_org_name"], out["to_org_name"], out["qty"], out["status"]) == (
        "Hospital B",
        "Hospital A",
        850,
        "CREATED",
    )
    assert (out["product_code"], out["priority"], out["carrier_org_id"]) == (
        "SURG-KIT-A",
        "CRITICAL",
        None,
    )
    assert (out["pickup"]["place"], out["pickup"]["lat"]) == ("Hospital B main store", 12.93)
    assert (out["drop"]["place"], out["drop"]["planned_at"]) == (
        "Hospital A main store",
        out["planned_eta"],
    )
    assert out["route_geometry"] is None and out["eta"] is None
    assert out["status_history"][0]["to_status"] == "CREATED"
    assert "landed_cost_paise" not in out and "unit_cost_paise" not in str(out)
    r = await (await client_for(world.users["b.STORE_MANAGER"])).get("/shipments")
    assert [s["id"] for s in r.json()["items"]] == [str(shipment.id)]


async def test_an_uninvolved_org_cannot_see_a_shipment(
    world: World, shipment: Shipment, client_for: ClientFor
) -> None:
    supplier = await client_for(world.users["s.SUPPLIER_DESK"])  # not a logistics org
    r = await supplier.get(f"/shipments/{shipment.id}")
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert (await supplier.get("/shipments")).json()["items"] == []
    r = await (await client_for(world.users["a.REQUESTER"])).get(f"/shipments/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_shipment_created_now_also_reaches_the_logistics_orgs(
    world: World, shipment: Shipment, session: AsyncSession, swiftmed: Fleet, other_fleet: Fleet
) -> None:
    (event,) = await outbox(session, EventType.SHIPMENT_CREATED)
    assert event["org_ids"] == ids(
        world.hospital_a, world.hospital_b, swiftmed.org, other_fleet.org
    )


# --- assign and unassign ----------------------------------------------------------------------


async def test_assign_stores_the_route_the_eta_and_the_carrier(
    session: AsyncSession,
    world: World,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
) -> None:
    before = datetime.now(UTC)
    r = await assign(dispatcher, shipment, swiftmed)
    after = datetime.now(UTC)
    assert r.status_code == 200, r.text
    out = r.json()
    km = await HaversineProvider().distance_km(B_STORE, A_STORE)
    assert out["status"] == "ASSIGNED"
    assert out["carrier_org_id"] == str(swiftmed.org.id)
    assert out["carrier_org_name"] == "SwiftMed Logistics"
    assert out["driver"] == {"id": str(swiftmed.drivers["Ravi"].id), "name": "Ravi"}
    assert out["vehicle"]["reg_no"] == swiftmed.van.reg_no
    assert out["route_provider"] == "HAVERSINE"
    assert out["route_distance_km"] == pytest.approx(km, abs=0.001)
    assert out["route_geometry"] == {
        "type": "LineString",
        "coordinates": [[77.62, 12.93], [77.59, 12.97]],
    }
    # §4: ETA = distance / 40 km/h + 1 h, counted from the assignment.
    eta = datetime.fromisoformat(out["eta"])
    hours = timedelta(hours=transport_eta_hours(km))
    assert before + hours <= eta <= after + hours
    assert out["drop"]["planned_at"] == out["eta"]
    assert [h["to_status"] for h in out["status_history"]] == ["CREATED", "ASSIGNED"]

    # One row in the dispatcher's org, mirrored into the requester's trail without the actor.
    rows = [a for a in await audit_of(session, shipment.id) if a.action.endswith("changed")]
    assert {(a.org_id, a.actor_id) for a in rows} == {
        (swiftmed.org.id, swiftmed.dispatcher.id),
        (world.hospital_a.id, None),
    }
    assert all((a.after or {})["status"] == "ASSIGNED" for a in rows)
    assert all(a.reason_source == "SYSTEM" for a in rows)
    (event,) = await outbox(session, EventType.SHIPMENT_STATUS_CHANGED)
    assert event["org_ids"] == ids(world.hospital_a, world.hospital_b, swiftmed.org)
    assert event["data"] == {"shipment_id": str(shipment.id), "from": "CREATED", "to": "ASSIGNED"}


async def test_once_assigned_other_logistics_orgs_no_longer_see_it(
    assigned: Shipment, other_dispatcher: httpx.AsyncClient
) -> None:
    assert (await other_dispatcher.get(f"/shipments/{assigned.id}")).status_code == 403
    assert (await other_dispatcher.get("/shipments")).json()["items"] == []


async def test_assign_needs_the_capability_and_own_drivers_and_vehicles(
    world: World,
    shipment: Shipment,
    client_for: ClientFor,
    other_dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
) -> None:
    hospital = await client_for(world.users["a.REQUESTER"])
    r = await assign(hospital, shipment, swiftmed)
    assert r.status_code == 403
    # Another logistics org cannot dispatch SwiftMed's driver or vehicle.
    r = await other_dispatcher.post(
        f"/shipments/{shipment.id}/assign",
        json={
            "driver_id": str(swiftmed.drivers["Ravi"].id),
            "vehicle_id": str(other_fleet.van.id),
        },
    )
    assert r.status_code == 403
    assert r.json()["message"] == "This driver belongs to another organization."
    r = await other_dispatcher.post(
        f"/shipments/{shipment.id}/assign",
        json={"driver_id": str(other_fleet.drivers["Omar"].id), "vehicle_id": str(swiftmed.van.id)},
    )
    assert r.status_code == 403
    # A hospital ADMIN has every capability but is not involved... it is the `to` org here,
    # yet has no drivers of its own.
    admin = await client_for(world.users["a.ADMIN"])
    r = await assign(admin, shipment, swiftmed)
    assert r.status_code == 403


async def test_assign_from_another_state_is_409(
    assigned: Shipment, dispatcher: httpx.AsyncClient, swiftmed: Fleet
) -> None:
    r = await assign(dispatcher, assigned, swiftmed)
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_transition"


async def test_an_inactive_driver_cannot_be_assigned(
    session: AsyncSession, shipment: Shipment, dispatcher: httpx.AsyncClient, swiftmed: Fleet
) -> None:
    swiftmed.drivers["Ravi"].active = False
    await session.flush()
    r = await assign(dispatcher, shipment, swiftmed)
    assert r.status_code == 400
    assert r.json()["message"] == "This driver is not active."


async def test_unassign_puts_it_back_on_the_board(
    session: AsyncSession,
    world: World,
    assigned: Shipment,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
) -> None:
    r = await dispatcher.post(f"/shipments/{assigned.id}/unassign", json={"reason": "Van broke"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["driver"], out["vehicle"], out["carrier_org_id"]) == (
        "CREATED",
        None,
        None,
        None,
    )
    assert (out["eta"], out["route_geometry"], out["route_provider"]) == (None, None, None)
    assert [h["to_status"] for h in out["status_history"]] == ["CREATED", "ASSIGNED", "CREATED"]
    events = await outbox(session, EventType.SHIPMENT_STATUS_CHANGED)
    assert events[-1]["data"]["to"] == "CREATED"
    assert events[-1]["org_ids"] == ids(
        world.hospital_a, world.hospital_b, swiftmed.org, other_fleet.org
    )
    (row,) = [
        a
        for a in await audit_of(session, assigned.id)
        if a.org_id == swiftmed.org.id and (a.after or {})["status"] == "CREATED"
    ]
    assert (row.reason, row.reason_source) == ("Van broke", "USER")
    # Unassigning again: CREATED -> CREATED is not in §8.
    r = await dispatcher.post(f"/shipments/{assigned.id}/unassign")
    assert r.status_code == 409


async def test_only_the_carrier_unassigns(
    assigned: Shipment, other_dispatcher: httpx.AsyncClient
) -> None:
    r = await other_dispatcher.post(f"/shipments/{assigned.id}/unassign")
    assert r.status_code == 403


async def test_assign_refuses_stock_held_at_more_than_one_facility(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    products: dict[str, Product],
    now: datetime,
    client_for: ClientFor,
    swiftmed: Fleet,
    other_fleet: Fleet,
    dispatcher: httpx.AsyncClient,
) -> None:
    """business-rules §8 limitation (until S16's route planner): one pickup per shipment."""
    annex = Facility(
        org_id=world.hospital_b.id, name="Hospital B annex", address="Annex", lat=12.91, lng=77.6
    )
    session.add(annex)
    await session.flush()
    # 100 expiring sooner at the annex: accept holds it first, then 750 at the main store.
    session.add(
        InventoryBatch(
            org_id=world.hospital_b.id,
            facility_id=annex.id,
            product_id=products["SURG-KIT-A"].id,
            batch_no="B-ANNEX",
            on_hand=100,
            expiry_date=now.date() + timedelta(days=120),
            unit_cost_paise=1500,
            last_verified_at=now - timedelta(hours=1),
        )
    )
    await session.flush()
    shipment = await approve_transfer(session, client_for, world, shortage)
    held = await holds_of(session, shipment)
    assert sorted(h.qty for h in held) == [100, 750]

    requester = await client_for(world.users["a.REQUESTER"])
    assert (await assign(requester, shipment, swiftmed)).status_code == 403  # no capability
    r = await assign(dispatcher, shipment, swiftmed)
    assert r.status_code == 409
    assert r.json() == {
        "code": "conflict",
        "message": "Stock is at more than one facility; plan it with the route planner",
        "details": {"reason": "multiple_pickup_facilities", "facility_count": 2},
    }
    await session.refresh(shipment)
    assert (shipment.status, shipment.carrier_org_id) == ("CREATED", None)


# --- cold chain -------------------------------------------------------------------------------


async def test_a_cold_chain_shipment_refuses_a_plain_vehicle_with_a_reason(
    session: AsyncSession, shipment: Shipment, dispatcher: httpx.AsyncClient, swiftmed: Fleet
) -> None:
    shipment.requires_cold_chain = True  # the product is 2-8 C (see test_cold_chain_flow)
    await session.flush()
    r = await assign(dispatcher, shipment, swiftmed)
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "validation"
    assert body["message"] == (
        "This shipment needs a cold-chain vehicle; "
        f"vehicle {swiftmed.van.reg_no} has no cold chain."
    )
    assert body["details"]["reason"] == "cold_chain_vehicle_required"
    await session.refresh(shipment)
    assert shipment.status == "CREATED"
    r = await assign(dispatcher, shipment, swiftmed, cold=True)
    assert r.status_code == 200, r.text
    assert r.json()["vehicle"]["has_cold_chain"] is True


# --- the driver's steps -----------------------------------------------------------------------


async def test_the_assigned_driver_moves_it_to_delivered(
    session: AsyncSession,
    world: World,
    assigned: Shipment,
    ravi: httpx.AsyncClient,
    swiftmed: Fleet,
) -> None:
    for status in ("PICKED_UP", "IN_TRANSIT", "DELIVERED"):
        r = await step(ravi, assigned, status)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
    out = r.json()
    assert [h["to_status"] for h in out["status_history"]] == [
        "CREATED",
        "ASSIGNED",
        "PICKED_UP",
        "IN_TRANSIT",
        "DELIVERED",
    ]
    assert out["pickup"]["actual_at"] is not None and out["drop"]["actual_at"] is not None
    events = await outbox(session, EventType.SHIPMENT_STATUS_CHANGED)
    assert [(e["data"]["from"], e["data"]["to"]) for e in events] == [
        ("CREATED", "ASSIGNED"),
        ("ASSIGNED", "PICKED_UP"),
        ("PICKED_UP", "IN_TRANSIT"),
        ("IN_TRANSIT", "DELIVERED"),
    ]
    assert all(
        e["org_ids"] == ids(world.hospital_a, world.hospital_b, swiftmed.org) for e in events
    )
    driver_rows = [
        a for a in await audit_of(session, assigned.id) if a.actor_id == swiftmed.users["Ravi"].id
    ]
    assert [(a.after or {})["status"] for a in driver_rows] == [
        "PICKED_UP",
        "IN_TRANSIT",
        "DELIVERED",
    ]
    assert all(a.reason == "No reason was entered." for a in driver_rows)


async def test_pickup_draws_down_the_source_stock_and_consumes_the_firm_hold(
    session: AsyncSession,
    world: World,
    assigned: Shipment,
    ravi: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    batch = await batch_of_b(session, world)
    (hold,) = await holds_of(session, assigned)
    assert (batch.on_hand, hold.status, hold.qty) == (2500, "FIRM", 850)
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    seen = (await manager_b.get("/inventory/batches")).json()["items"][0]
    assert (seen["transferable"], seen["held_qty"]) == (150, 850)

    r = await step(ravi, assigned, "PICKED_UP")
    assert r.status_code == 200, r.text

    batch = await batch_of_b(session, world)
    (hold,) = await holds_of(session, assigned)
    assert (batch.on_hand, hold.status) == (1650, "CONSUMED")
    # B's transferable is unchanged: the stock that left was already held.
    seen = (await manager_b.get("/inventory/batches")).json()["items"][0]
    assert (seen["on_hand"], seen["transferable"], seen["held_qty"]) == (1650, 150, 0)
    r = await manager_b.get("/source-requests", params={"direction": "incoming"})
    (sr,) = r.json()["items"]
    assert (sr["status"], sr["held_qty"]) == ("CONFIRMED", 0)

    # Audit: SYSTEM rows in the source org with a factual cause, never the driver's id.
    (stock_row,) = [
        a for a in await audit_of(session, batch.id) if a.action == "inventory_batch.picked_up"
    ]
    (hold_row,) = [a for a in await audit_of(session, hold.id) if a.after == {"status": "CONSUMED"}]
    for row in (stock_row, hold_row):
        assert (row.org_id, row.actor_id, row.reason_source) == (
            world.hospital_b.id,
            None,
            "SYSTEM",
        )
        assert row.reason == "The driver recorded the pickup of this shipment."
    after = stock_row.after or {}
    assert (stock_row.before, after["on_hand"]) == ({"on_hand": 2500}, 1650)
    assert after["shipment_id"] == str(assigned.id)
    (changed,) = await outbox(session, EventType.INVENTORY_CHANGED)
    assert changed["org_ids"] == ids(world.hospital_b)
    assert changed["data"]["batch_ids"] == [str(batch.id)]
    # The consumed hold is not a release: no waiting shortage is re-run for it.
    released = await session.scalar(
        select(Hold).where(Hold.status == "RELEASED", Hold.batch_id == batch.id)
    )
    assert released is None


async def test_a_short_pickup_draws_down_what_the_batch_records_and_the_receipt_shows_the_gap(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    assigned: Shipment,
    ravi: httpx.AsyncClient,
    omar: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    """business-rules §8/§9: a batch recording less on hand than its FIRM hold no longer
    blocks pickup. It draws down what the batch records (never below 0), consumes the hold,
    and the SYSTEM rows in the source org state the recorded figures."""
    batch = await batch_of_b(session, world)
    batch.on_hand = 800  # recounted below the 850 held, e.g. by a verify
    await session.flush()
    assert (await step(omar, assigned, "PICKED_UP")).status_code == 403  # another org
    r = await step(ravi, assigned, "PICKED_UP")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PICKED_UP"
    assert (await step(ravi, assigned, "PICKED_UP")).status_code == 409  # already picked up

    batch = await batch_of_b(session, world)
    (hold,) = await holds_of(session, assigned)
    assert (batch.on_hand, hold.status, hold.qty) == (0, "CONSUMED", 850)
    cause = (
        "The driver recorded the pickup of this shipment. The batch recorded 800 on hand, "
        "less than the 850 held, so 800 was drawn down."
    )
    (stock_row,) = [
        a for a in await audit_of(session, batch.id) if a.action == "inventory_batch.picked_up"
    ]
    (hold_row,) = [a for a in await audit_of(session, hold.id) if a.after == {"status": "CONSUMED"}]
    for row in (stock_row, hold_row):
        assert (row.org_id, row.actor_id, row.reason_source, row.reason) == (
            world.hospital_b.id,
            None,
            "SYSTEM",
            cause,
        )
    assert stock_row.before == {"on_hand": 800}
    after = stock_row.after or {}
    assert (after["on_hand"], after["held_qty"], after["drawn_qty"]) == (0, 850, 800)

    # The shortfall surfaces at receipt; reconciliation opens the residual (§9).
    for status in ("IN_TRANSIT", "DELIVERED"):
        assert (await step(ravi, assigned, status)).status_code == 200
    receiver = await client_for(world.users["a.RECEIVER"])
    r = await receiver.post(
        f"/shipments/{assigned.id}/receipt",
        json={
            "received": 800,
            "accepted": 800,
            "rejected": 0,
            "condition": "GOOD",
            "expiry_date": "2027-03-31",
        },
    )
    assert r.status_code == 201, r.text
    reconciliation = r.json()["reconciliation"]
    assert (reconciliation["discrepancy"], reconciliation["outcome"]) == (50, "PARTIAL")
    residual = await session.get_one(Shortage, reconciliation["residual_shortage_id"])
    assert (residual.shortfall, residual.parent_shortage_id) == (50, shortage.id)


async def test_a_driver_who_is_not_assigned_or_from_another_org_gets_403(
    world: World,
    assigned: Shipment,
    priya: httpx.AsyncClient,
    omar: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    r = await step(priya, assigned, "PICKED_UP")  # same org, not assigned
    assert r.status_code == 403
    assert r.json()["message"] == "Only the shipment's assigned driver can do this."
    r = await step(omar, assigned, "PICKED_UP")  # another logistics org
    assert r.status_code == 403
    # A hospital ADMIN holds shipment.update_status (ADMIN has every capability) but is no driver.
    r = await step(await client_for(world.users["a.ADMIN"]), assigned, "PICKED_UP")
    assert r.status_code == 403
    r = await step(await client_for(world.users["a.REQUESTER"]), assigned, "PICKED_UP")
    assert r.status_code == 403  # no capability


async def test_skipping_a_state_is_409(
    session: AsyncSession,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    swiftmed: Fleet,
) -> None:
    r = await step(ravi, shipment, "PICKED_UP")  # CREATED -> PICKED_UP
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_transition"
    assert (await assign(dispatcher, shipment, swiftmed)).status_code == 200
    for status in ("IN_TRANSIT", "DELIVERED", "CREATED", "ASSIGNED", "RECONCILED"):
        r = await step(ravi, shipment, status)
        assert r.status_code == 409, status
    assert (await step(ravi, shipment, "PICKED_UP")).status_code == 200
    assert (await step(ravi, shipment, "PICKED_UP")).status_code == 409
    for status in ("IN_TRANSIT", "DELIVERED"):
        assert (await step(ravi, shipment, status)).status_code == 200
    # DELIVERED -> RECONCILED is reconciliation's (S12), not the driver's.
    assert (await step(ravi, shipment, "RECONCILED")).status_code == 409
    await session.refresh(shipment)
    assert shipment.status == "DELIVERED"


async def test_a_purchase_order_shipment_moves_without_touching_inventory(
    session: AsyncSession,
    world: World,
    shortage: object,
    s1: Orgs,
    client_for: ClientFor,
    swiftmed: Fleet,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
) -> None:
    """Scenario 1 step 6: Supplier Y dispatches; a driver is assigned, picks up, delivers."""
    from app.purchase_orders.models import PurchaseOrder
    from app.recommendations.tests.conftest import answer_b, open_rec
    from app.source_requests.tests.conftest import add_user

    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "decline")  # type: ignore[arg-type]
    rec = await open_rec(session, shortage)  # type: ignore[arg-type]
    r = await (await client_for(world.users["a.APPROVER"])).post(
        f"/recommendations/{rec.id}/approve", json={}
    )
    po = await session.get_one(PurchaseOrder, r.json()["purchase_order_id"])
    desk = await client_for(await add_user(session, s1["Supplier Y"], "SUPPLIER_DESK", "d@y.t"))
    for action in ("acknowledge", "dispatch"):
        assert (await desk.post(f"/purchase-orders/{po.id}/{action}", json={})).status_code == 200
    shipment = await session.scalar(select(Shipment).where(Shipment.purchase_order_id == po.id))
    assert shipment is not None
    r = await desk.get(f"/shipments/{shipment.id}")
    assert r.json()["pickup"]["place"] == "Supplier Y"
    assert (await assign(dispatcher, shipment, swiftmed)).status_code == 200
    for status in ("PICKED_UP", "IN_TRANSIT", "DELIVERED"):
        assert (await step(ravi, shipment, status)).status_code == 200
    assert await outbox(session, EventType.INVENTORY_CHANGED) == []


# --- location pings ---------------------------------------------------------------------------


async def test_the_assigned_driver_shares_location(
    session: AsyncSession,
    world: World,
    assigned: Shipment,
    ravi: httpx.AsyncClient,
    client_for: ClientFor,
    swiftmed: Fleet,
) -> None:
    ts = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=5)
    r = await ravi.post(
        f"/shipments/{assigned.id}/location",
        json={"lat": 12.95, "lng": 77.61, "ts": ts.isoformat()},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "shipment_id": str(assigned.id),
        "lat": 12.95,
        "lng": 77.61,
        "ts": ts.isoformat().replace("+00:00", "Z"),
    }
    (event,) = await outbox(session, EventType.SHIPMENT_LOCATION)
    assert event["org_ids"] == ids(world.hospital_a, world.hospital_b, swiftmed.org)
    assert event["data"]["lat"] == 12.95 and event["data"]["lng"] == 77.61
    r = await ravi.post(f"/shipments/{assigned.id}/location", json={"lat": 12.96, "lng": 77.6})
    assert r.status_code == 200
    pings = list(await session.scalars(select(LocationPing)))
    assert len(pings) == 2
    out = (
        await (await client_for(world.users["a.REQUESTER"])).get(f"/shipments/{assigned.id}")
    ).json()
    assert (out["last_location"]["lat"], out["last_location"]["lng"]) == (12.96, 77.6)
    # Pings write no audit row (not a state change).
    pinged = await session.scalars(select(AuditLog).where(AuditLog.entity == "location_ping"))
    assert list(pinged) == []


async def test_only_the_assigned_driver_pings_and_only_on_the_way(
    assigned: Shipment,
    ravi: httpx.AsyncClient,
    priya: httpx.AsyncClient,
    omar: httpx.AsyncClient,
) -> None:
    body = {"lat": 12.95, "lng": 77.61}
    assert (await priya.post(f"/shipments/{assigned.id}/location", json=body)).status_code == 403
    assert (await omar.post(f"/shipments/{assigned.id}/location", json=body)).status_code == 403
    r = await ravi.post(f"/shipments/{assigned.id}/location", json={"lat": 91, "lng": 0})
    assert r.status_code == 422
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = await ravi.post(f"/shipments/{assigned.id}/location", json={**body, "ts": future})
    assert r.status_code == 400
    for status in ("PICKED_UP", "IN_TRANSIT", "DELIVERED"):
        assert (await step(ravi, assigned, status)).status_code == 200
    r = await ravi.post(f"/shipments/{assigned.id}/location", json=body)
    assert r.status_code == 409


async def test_the_detail_shows_only_pings_since_the_current_assignment(
    session: AsyncSession,
    world: World,
    assigned: Shipment,
    ravi: httpx.AsyncClient,
    omar: httpx.AsyncClient,
    dispatcher: httpx.AsyncClient,
    other_dispatcher: httpx.AsyncClient,
    other_fleet: Fleet,
    client_for: ClientFor,
) -> None:
    """An earlier carrier's driver position never reaches the dispatch board or the next
    carrier, even a ping stamped (by the phone) up to 5 min ahead."""
    ahead = (datetime.now(UTC) + timedelta(minutes=4)).isoformat()
    r = await ravi.post(
        f"/shipments/{assigned.id}/location", json={"lat": 12.95, "lng": 77.61, "ts": ahead}
    )
    assert r.status_code == 200
    requester = await client_for(world.users["a.REQUESTER"])
    detail = (await requester.get(f"/shipments/{assigned.id}")).json()
    assert detail["last_location"]["lat"] == 12.95

    assert (await dispatcher.post(f"/shipments/{assigned.id}/unassign")).status_code == 200
    # CREATED: no position for anyone, the dispatch board included.
    for client in (other_dispatcher, requester):
        r = await client.get(f"/shipments/{assigned.id}")
        assert r.status_code == 200
        assert r.json()["last_location"] is None

    r = await other_dispatcher.post(
        f"/shipments/{assigned.id}/assign",
        json={
            "driver_id": str(other_fleet.drivers["Omar"].id),
            "vehicle_id": str(other_fleet.van.id),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["last_location"] is None  # Ravi's ping stays with SwiftMed's trip
    assert (await dispatcher.get(f"/shipments/{assigned.id}")).status_code == 403  # left it
    r = await omar.post(f"/shipments/{assigned.id}/location", json={"lat": 12.94, "lng": 77.6})
    assert r.status_code == 200
    detail = (await other_dispatcher.get(f"/shipments/{assigned.id}")).json()
    assert (detail["last_location"]["lat"], detail["last_location"]["lng"]) == (12.94, 77.6)
    # Ravi is no longer the driver: 403; and the stored pings are both still there.
    assert (
        await ravi.post(f"/shipments/{assigned.id}/location", json={"lat": 1, "lng": 1})
    ).status_code == 403
    assert len(list(await session.scalars(select(LocationPing)))) == 2


async def test_driver_jobs_lists_only_my_shipments(
    assigned: Shipment, ravi: httpx.AsyncClient, priya: httpx.AsyncClient
) -> None:
    r = await ravi.get("/shipments", params={"assigned_to_me": "true"})
    assert [s["id"] for s in r.json()["items"]] == [str(assigned.id)]
    r = await priya.get("/shipments", params={"assigned_to_me": "true"})
    assert r.json()["items"] == []


async def test_driver_jobs_are_empty_for_a_user_who_is_not_a_driver(
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    world: World,
    client_for: ClientFor,
) -> None:
    """Never `driver_id IS NULL`: the dispatcher sees the CREATED shipment on the board,
    but has no driver jobs."""
    r = await dispatcher.get("/shipments", params={"status": "CREATED"})
    assert [s["id"] for s in r.json()["items"]] == [str(shipment.id)]
    r = await dispatcher.get("/shipments", params={"assigned_to_me": "true"})
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None}
    # The receiving hospital's user sees the shipment, but has no driver jobs either.
    requester = await client_for(world.users["a.REQUESTER"])
    r = await requester.get("/shipments", params={"assigned_to_me": "true"})
    assert r.json()["items"] == []


# --- fleet ------------------------------------------------------------------------------------


async def test_dispatchers_list_their_own_drivers_and_vehicles(
    world: World,
    client_for: ClientFor,
    dispatcher: httpx.AsyncClient,
    other_dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
) -> None:
    r = await dispatcher.get("/drivers")
    assert r.status_code == 200, r.text
    assert sorted(d["name"] for d in r.json()["items"]) == ["Priya", "Ravi"]
    assert all(set(d) == {"id", "user_id", "name", "phone", "active"} for d in r.json()["items"])
    r = await dispatcher.get("/vehicles")
    assert sorted((v["reg_no"], v["has_cold_chain"]) for v in r.json()["items"]) == sorted(
        [(swiftmed.van.reg_no, False), (swiftmed.cold_van.reg_no, True)]  # type: ignore[union-attr]
    )
    r = await other_dispatcher.get("/drivers")
    assert [d["name"] for d in r.json()["items"]] == ["Omar"]
    hospital = await client_for(world.users["a.REQUESTER"])
    assert (await hospital.get("/drivers")).status_code == 403
    assert (await hospital.get("/vehicles")).status_code == 403


async def test_legs_are_the_pickup_and_the_drop(session: AsyncSession, assigned: Shipment) -> None:
    legs = list(
        await session.scalars(
            select(ShipmentLeg)
            .where(ShipmentLeg.shipment_id == assigned.id)
            .order_by(ShipmentLeg.seq)
        )
    )
    assert [(leg.seq, leg.stop_type) for leg in legs] == [(1, "PICKUP"), (2, "DROP")]
    assert legs[1].planned_at == assigned.eta
