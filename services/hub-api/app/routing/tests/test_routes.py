"""S16 endpoints: POST /routes/optimize (a stop order for one driver, infeasible shipments
with reasons, writes nothing) and POST /routes/apply (assigns the feasible shipments
through S11's assignment, with the plan's planned_at and ETA, audited). Each covers
success, 403 from another org, and 409 on the §8 state machine."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.auth.models import User
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.errors import AppError
from app.orgs.models import Organization, OrgType
from app.purchase_orders.models import PurchaseOrder
from app.recommendations.tests.conftest import audit_of, outbox
from app.shipments import service as shipments
from app.shipments.models import Shipment, ShipmentLeg
from app.shipments.tests.conftest import Fleet, add_fleet
from app.shortages.models import Shortage
from app.source_requests.tests.conftest import add_org, facility_of

pytestmark = pytest.mark.anyio

IST = "Asia/Kolkata"


@pytest.fixture
async def swiftmed(session: AsyncSession) -> Fleet:
    return await add_fleet(
        session, "SwiftMed Logistics", "swiftmed.test", ["Ravi", "Priya"], cold=True
    )


@pytest.fixture
async def other_fleet(session: AsyncSession) -> Fleet:
    return await add_fleet(session, "Other Logistics", "other.test", ["Omar"], cold=False)


@pytest.fixture
async def dispatcher(client_for: ClientFor, swiftmed: Fleet) -> httpx.AsyncClient:
    return await client_for(swiftmed.dispatcher)


@pytest.fixture
async def other_dispatcher(client_for: ClientFor, other_fleet: Fleet) -> httpx.AsyncClient:
    return await client_for(other_fleet.dispatcher)


class Maker:
    """Unassigned purchase-order shipments from suppliers placed where a test wants them,
    to a hospital's main store, due at a chosen time."""

    def __init__(self, session: AsyncSession, world: World, products: dict[str, Product]):
        self.session, self.world, self.products = session, world, products
        self.n = 0

    async def __call__(
        self,
        *,
        due_in: timedelta,
        at: tuple[float, float] = (12.93, 77.62),
        to: Organization | None = None,
        cold: bool = False,
    ) -> Shipment:
        self.n += 1
        hospital = to or self.world.hospital_a
        product = self.products["DIAG-RDK" if cold else "SURG-KIT-A"]
        requester: User = self.world.users["a.REQUESTER"]
        supplier = await add_org(
            self.session, f"Supplier R{self.n}", OrgType.SUPPLIER, at[0], at[1]
        )
        shortage = Shortage(
            org_id=hospital.id,
            facility_id=(await facility_of(self.session, hospital)).id,
            product_id=product.id,
            qty_required=10,
            qty_local_usable=0,
            shortfall=10,
            required_by=datetime.now(UTC) + due_in,
            priority="ROUTINE",
            min_shelf_life_days=30,
            status="IN_FULFILLMENT",
            created_by=requester.id,
            source="FORM",
        )
        self.session.add(shortage)
        await self.session.flush()
        po = PurchaseOrder(
            id=uuid.uuid4(),
            shortage_id=shortage.id,
            supplier_org_id=supplier.id,
            product_id=product.id,
            qty=10,
            unit_price_paise=1000,
            status="DISPATCHED",
            eta=shortage.required_by,
        )
        self.session.add(po)
        await self.session.flush()
        return await shipments.create(
            self.session,
            shortage,
            from_org_id=supplier.id,
            qty=10,
            planned_eta=None,
            actor=None,
            reason="The supplier dispatched the order.",
            purchase_order_id=po.id,
        )


@pytest.fixture
def make(session: AsyncSession, world: World, products: dict[str, Product]) -> Maker:
    return Maker(session, world, products)


@pytest.fixture
async def three(make: Maker, world: World) -> list[Shipment]:
    """Three unassigned shipments around Bangalore, due later today."""
    return [
        await make(due_in=timedelta(hours=8), at=(12.93, 77.62)),
        await make(due_in=timedelta(hours=6), at=(13.01, 77.55), to=world.hospital_b),
        await make(due_in=timedelta(hours=10), at=(12.90, 77.70)),
    ]


def body(fleet: Fleet, rows: list[Shipment], *, cold: bool = False, **extra: Any) -> dict[str, Any]:
    vehicle = fleet.cold_van if cold else fleet.van
    assert vehicle is not None
    return {
        "driver_id": str(fleet.drivers["Ravi"].id),
        "vehicle_id": str(vehicle.id),
        "shipment_ids": [str(s.id) for s in rows],
        "timezone": IST,
        **extra,
    }


def check_plan(out: dict[str, Any], rows: list[Shipment]) -> None:
    """Every pickup before its drop, ETAs in order, each drop by its deadline."""
    seen: set[str] = set()
    etas = [datetime.fromisoformat(s["eta"]) for s in out["stops"]]
    assert etas == sorted(etas)
    assert [s["seq"] for s in out["stops"]] == list(range(1, len(out["stops"]) + 1))
    for stop in out["stops"]:
        if stop["type"] == "PICKUP":
            seen.add(stop["shipment_id"])
        else:
            assert stop["shipment_id"] in seen


async def count(session: AsyncSession, model: type[Any]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


# --- POST /routes/optimize ----------------------------------------------------------------------


async def test_optimize_plans_three_shipments_with_each_pickup_before_its_drop(
    session: AsyncSession, dispatcher: httpx.AsyncClient, swiftmed: Fleet, three: list[Shipment]
) -> None:
    audits = await count(session, AuditLog)
    before = datetime.now(UTC)
    r = await dispatcher.post("/routes/optimize", json=body(swiftmed, three))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["infeasible"] == []
    assert out["driver_id"] == str(swiftmed.drivers["Ravi"].id)
    assert len(out["stops"]) == 6
    assert {s["shipment_id"] for s in out["stops"]} == {str(s.id) for s in three}
    check_plan(out, three)
    first = out["stops"][0]
    assert first["type"] == "PICKUP" and datetime.fromisoformat(first["eta"]) >= before
    assert first["place"].startswith("Supplier R")
    drops = {s["shipment_id"]: s for s in out["stops"] if s["type"] == "DROP"}
    assert drops[str(three[1].id)]["place"] == "Hospital B main store"
    assert (drops[str(three[0].id)]["lat"], drops[str(three[0].id)]["lng"]) == (12.97, 77.59)
    # Writes nothing.
    assert await count(session, AuditLog) == audits
    for s in three:
        await session.refresh(s)
        assert s.status == "CREATED" and s.driver_id is None


async def test_optimize_reports_a_deadline_that_cannot_be_met_with_a_reason(
    dispatcher: httpx.AsyncClient, swiftmed: Fleet, make: Maker, three: list[Shipment]
) -> None:
    late = await make(due_in=timedelta(minutes=30))  # under the 1 h handover alone
    r = await dispatcher.post("/routes/optimize", json=body(swiftmed, [*three, late]))
    assert r.status_code == 200, r.text
    out = r.json()
    (bad,) = out["infeasible"]
    assert bad["shipment_id"] == str(late.id)
    assert bad["reason"].startswith("Cannot reach Hospital A main store before ")
    assert " IST: even going straight there, the earliest arrival is " in bad["reason"]
    assert str(late.id) not in {s["shipment_id"] for s in out["stops"]}
    assert len(out["stops"]) == 6


async def test_optimize_reports_cold_chain_shipments_that_cannot_ride_or_have_no_cold_vehicle(
    dispatcher: httpx.AsyncClient, swiftmed: Fleet, make: Maker
) -> None:
    near = await make(due_in=timedelta(hours=8), cold=True)
    far = await make(due_in=timedelta(hours=24), at=(12.30, 76.64), cold=True)  # Mysuru
    r = await dispatcher.post("/routes/optimize", json=body(swiftmed, [near, far], cold=True))
    assert r.status_code == 200, r.text
    (bad,) = r.json()["infeasible"]
    assert bad["shipment_id"] == str(far.id)
    assert bad["reason"].startswith("The ride from Supplier R2 to Hospital A main store takes ")
    assert bad["reason"].endswith("with the handover, over the 4 h cold-chain limit.")
    # In a van without cold chain, neither can go.
    r = await dispatcher.post("/routes/optimize", json=body(swiftmed, [near]))
    assert r.status_code == 200, r.text
    (bad,) = r.json()["infeasible"]
    assert "needs a cold-chain vehicle" in bad["reason"] and r.json()["stops"] == []


async def test_optimize_without_a_vehicle_and_in_utc(
    dispatcher: httpx.AsyncClient, swiftmed: Fleet, three: list[Shipment]
) -> None:
    payload = body(swiftmed, three)
    del payload["vehicle_id"], payload["timezone"]
    r = await dispatcher.post("/routes/optimize", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["vehicle_id"] is None and len(r.json()["stops"]) == 6
    r = await dispatcher.post("/routes/optimize", json={**payload, "timezone": "Mars/Olympus_Mons"})
    assert r.status_code == 422
    r = await dispatcher.post("/routes/optimize", json={**payload, "shipment_ids": []})
    assert r.status_code == 422


async def test_optimize_refuses_another_orgs_driver_vehicle_or_shipment(
    world: World,
    client_for: ClientFor,
    dispatcher: httpx.AsyncClient,
    other_dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
    three: list[Shipment],
) -> None:
    # Another logistics org's dispatcher with SwiftMed's driver.
    r = await other_dispatcher.post("/routes/optimize", json=body(swiftmed, three))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    # SwiftMed's driver with the other org's van.
    payload = {**body(swiftmed, three), "vehicle_id": str(other_fleet.van.id)}
    assert (await dispatcher.post("/routes/optimize", json=payload)).status_code == 403
    # A shipment the other org has taken is no longer SwiftMed's to see.
    taken = three[0]
    r = await other_dispatcher.post(
        f"/shipments/{taken.id}/assign",
        json={
            "driver_id": str(other_fleet.drivers["Omar"].id),
            "vehicle_id": str(other_fleet.van.id),
        },
    )
    assert r.status_code == 200, r.text
    r = await dispatcher.post("/routes/optimize", json=body(swiftmed, three))
    assert r.status_code == 403
    # Without shipment.assign.
    requester = await client_for(world.users["a.REQUESTER"])
    assert (await requester.post("/routes/optimize", json=body(swiftmed, three))).status_code == 403
    r = await dispatcher.post(
        "/routes/optimize", json={**body(swiftmed, three[1:]), "driver_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404


async def test_optimize_is_409_unless_every_shipment_is_unassigned(
    dispatcher: httpx.AsyncClient, swiftmed: Fleet, three: list[Shipment]
) -> None:
    r = await dispatcher.post(
        f"/shipments/{three[0].id}/assign",
        json={"driver_id": str(swiftmed.drivers["Priya"].id), "vehicle_id": str(swiftmed.van.id)},
    )
    assert r.status_code == 200, r.text
    r = await dispatcher.post("/routes/optimize", json=body(swiftmed, three))
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_transition"
    assert r.json()["details"] == {"shipment_id": str(three[0].id), "status": "ASSIGNED"}


# --- POST /routes/apply -------------------------------------------------------------------------


async def test_apply_assigns_the_feasible_shipments_with_the_plans_times_and_audit_rows(
    session: AsyncSession,
    world: World,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    make: Maker,
    three: list[Shipment],
) -> None:
    late = await make(due_in=timedelta(minutes=30))
    r = await dispatcher.post(
        "/routes/apply", json=body(swiftmed, [*three, late], reason="Morning round")
    )
    assert r.status_code == 200, r.text
    out = r.json()
    check_plan(out, three)
    assert [i["shipment_id"] for i in out["infeasible"]] == [str(late.id)]
    pickups = [s["shipment_id"] for s in out["stops"] if s["type"] == "PICKUP"]
    assert out["assigned_shipment_ids"] == pickups
    assert set(pickups) == {str(s.id) for s in three}
    stops = {(s["shipment_id"], s["type"]): s for s in out["stops"]}
    ravi, van = swiftmed.drivers["Ravi"], swiftmed.van
    for s in three:
        await session.refresh(s)
        assert (s.status, s.driver_id, s.vehicle_id) == ("ASSIGNED", ravi.id, van.id)
        assert s.carrier_org_id == swiftmed.org.id
        drop_eta = datetime.fromisoformat(stops[(str(s.id), "DROP")]["eta"])
        assert s.eta == drop_eta
        legs = {
            leg.stop_type: leg
            for leg in await session.scalars(
                select(ShipmentLeg)
                .where(ShipmentLeg.shipment_id == s.id)
                .execution_options(populate_existing=True)
            )
        }
        assert legs["PICKUP"].planned_at == datetime.fromisoformat(
            stops[(str(s.id), "PICKUP")]["eta"]
        )
        assert legs["DROP"].planned_at == drop_eta
        # Rule 5: the assignment's audit row, in the carrier's org, mirrored to the receiver.
        rows = [a for a in await audit_of(session, s.id) if a.action == "shipment.status_changed"]
        mine = [a for a in rows if a.org_id == swiftmed.org.id]
        assert len(mine) == 1
        (row,) = mine
        assert row.actor_id == swiftmed.dispatcher.id
        assert (row.reason, row.reason_source) == ("Morning round", "USER")
        assert row.after is not None
        assert row.after["status"] == "ASSIGNED" and row.after["driver_id"] == str(ravi.id)
        assert row.after["planned_pickup_at"] == legs["PICKUP"].planned_at.isoformat()
        assert any(a.org_id == s.to_org_id and a.actor_id is None for a in rows)
    await session.refresh(late)
    assert late.status == "CREATED" and late.driver_id is None
    changed = await outbox(session, EventType.SHIPMENT_STATUS_CHANGED)
    assert sorted(e["data"]["shipment_id"] for e in changed) == sorted(str(s.id) for s in three)


async def test_apply_without_a_reason_records_a_system_reason(
    session: AsyncSession, dispatcher: httpx.AsyncClient, swiftmed: Fleet, three: list[Shipment]
) -> None:
    r = await dispatcher.post("/routes/apply", json=body(swiftmed, three[:1]))
    assert r.status_code == 200, r.text
    (row,) = [
        a
        for a in await audit_of(session, three[0].id)
        if a.action == "shipment.status_changed" and a.org_id == swiftmed.org.id
    ]
    assert (row.reason, row.reason_source) == ("No reason was entered.", "SYSTEM")


async def test_apply_needs_a_vehicle_of_the_callers_org(
    dispatcher: httpx.AsyncClient,
    other_dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    other_fleet: Fleet,
    three: list[Shipment],
) -> None:
    payload = body(swiftmed, three)
    del payload["vehicle_id"]
    assert (await dispatcher.post("/routes/apply", json=payload)).status_code == 422
    r = await dispatcher.post(
        "/routes/apply", json={**payload, "vehicle_id": str(other_fleet.van.id)}
    )
    assert r.status_code == 403
    r = await other_dispatcher.post("/routes/apply", json=body(swiftmed, three))
    assert r.status_code == 403
    r = await other_dispatcher.post(
        "/routes/apply",
        json={
            **body(swiftmed, three),
            "driver_id": str(other_fleet.drivers["Omar"].id),
            "vehicle_id": str(other_fleet.van.id),
        },
    )
    assert r.status_code == 200, r.text  # the other org's own driver and van: fine


async def test_apply_is_409_for_an_assigned_shipment_and_when_nothing_fits(
    session: AsyncSession,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    make: Maker,
    three: list[Shipment],
) -> None:
    r = await dispatcher.post("/routes/apply", json=body(swiftmed, three[:1]))
    assert r.status_code == 200, r.text
    r = await dispatcher.post("/routes/apply", json=body(swiftmed, three))
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"
    for s in three[1:]:
        await session.refresh(s)
        assert s.status == "CREATED"
    late = await make(due_in=timedelta(minutes=30))
    r = await dispatcher.post("/routes/apply", json=body(swiftmed, [late]))
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert r.json()["details"]["infeasible"][0]["shipment_id"] == str(late.id)
    await session.refresh(late)
    assert late.status == "CREATED"


async def test_apply_keeps_nothing_if_one_assignment_is_refused(
    session: AsyncSession,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    three: list[Shipment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = shipments.assign
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> Shipment:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AppError(409, "conflict", "Refused for the test.")
        return await real(*args, **kwargs)

    monkeypatch.setattr(shipments, "assign", flaky)
    audits = await count(session, AuditLog)
    r = await dispatcher.post("/routes/apply", json=body(swiftmed, three))
    assert r.status_code == 409
    assert calls == 2
    assert await count(session, AuditLog) == audits
    for s in three:
        await session.refresh(s)
        assert s.status == "CREATED" and s.driver_id is None
