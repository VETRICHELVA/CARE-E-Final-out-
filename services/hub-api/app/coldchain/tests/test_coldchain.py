"""S15: cold-chain rules on ingested readings, DEVICE_SILENT from the worker, the
`coldchain.*` events, the audit rows, GET /shipments/{id}/coldchain, and the receipt's
inspection note once an excursion is on record.

The S11 fixtures move Surgical Kit A from Hospital B to Hospital A; `band` gives that product
Scenario 2's 2–8 °C, so the same shipment carries a cold-chain rule."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.coldchain import service as coldchain
from app.coldchain.models import ColdChainEvent
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.iot import service as iot
from app.iot.models import Device
from app.iot.schemas import ReadingIn, TelemetryBatch
from app.receiving.tests.conftest import receipt
from app.recommendations.tests.conftest import outbox
from app.shipments import service as shipments
from app.shipments.models import Shipment
from app.shipments.tests import conftest as s11
from app.shipments.tests.conftest import Fleet, step

pytestmark = pytest.mark.anyio

now = s11.now
s1 = s11.s1
shortage = s11.shortage
swiftmed = s11.swiftmed
other_fleet = s11.other_fleet
shipment = s11.shipment
assigned = s11.assigned
dispatcher = s11.dispatcher
other_dispatcher = s11.other_dispatcher
ravi = s11.ravi

T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)
COLD_EVENTS = (
    EventType.COLDCHAIN_EXCURSION,
    EventType.COLDCHAIN_RECOVERED,
    EventType.COLDCHAIN_DEVICE_SILENT,
)


@pytest.fixture
async def band(session: AsyncSession, shipment: Shipment) -> Product:
    product = await session.get_one(Product, shipment.product_id)
    product.temp_min_c, product.temp_max_c = 2.0, 8.0
    await session.flush()
    return product


@pytest.fixture
async def box(session: AsyncSession, swiftmed: Fleet) -> Device:
    device = Device(org_id=swiftmed.org.id, device_id="cb-01")
    session.add(device)
    await session.flush()
    return device


@pytest.fixture
async def on_the_road(
    session: AsyncSession,
    assigned: Shipment,
    band: Product,
    box: Device,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
) -> Shipment:
    """Scenario 2 step 3 onwards: cb-01 rides with the shipment, now IN_TRANSIT."""
    r = await dispatcher.post(f"/devices/{box.id}/assign", json={"shipment_id": str(assigned.id)})
    assert r.status_code == 200, r.text
    for status in ("PICKED_UP", "IN_TRANSIT"):
        assert (await step(ravi, assigned, status)).status_code == 200
    await session.refresh(assigned)
    return assigned


class Box:
    """cb-01 publishing every 10 s from T0; each `send` is one ingest batch."""

    def __init__(self, session: AsyncSession, name: str = "cb-01") -> None:
        self.session, self.name, self.n = session, name, 0

    async def send(self, *temps: float) -> list[ColdChainEvent]:
        batch = TelemetryBatch(
            readings=[
                ReadingIn(
                    device_id=self.name,
                    ts=T0 + timedelta(seconds=10 * (self.n + i)),
                    temp_c=t,
                    battery=80,
                )
                for i, t in enumerate(temps)
            ]
        )
        self.n += len(temps)
        before = set(await self.session.scalars(select(ColdChainEvent.id)))
        assert (await iot.ingest(self.session, batch)).stored == len(temps)
        rows = await self.session.scalars(select(ColdChainEvent).order_by(ColdChainEvent.ts))
        return [e for e in rows if e.id not in before]


async def events_of(session: AsyncSession, shipment: Shipment) -> list[ColdChainEvent]:
    stmt = select(ColdChainEvent).where(ColdChainEvent.shipment_id == shipment.id)
    return list(await session.scalars(stmt.order_by(ColdChainEvent.ts)))


async def rows_of(session: AsyncSession, event: ColdChainEvent) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_id == event.id)
    return list(await session.scalars(stmt.order_by(AuditLog.ts, AuditLog.id)))


# --- EXCURSION and RECOVERED on ingested readings ----------------------------------------------


async def test_scenario_2_excursion_then_recovered(
    session: AsyncSession, world: World, on_the_road: Shipment, swiftmed: Fleet
) -> None:
    """Readings near 4 °C, then 9.1 and 9.4 °C (each in its own batch, as the ingest posts
    every 2 s) -> EXCURSION; back in range for 2 readings -> RECOVERED; the excursion stays."""
    cb = Box(session)
    assert await cb.send(4.1, 4.3) == []
    assert await cb.send(9.1) == []  # one reading out of range is not yet an excursion
    (excursion,) = await cb.send(9.4)
    assert (excursion.type, excursion.severity, excursion.device_id) == (
        "EXCURSION",
        "ALERT",
        "cb-01",
    )
    assert (excursion.observed_value, excursion.threshold) == (9.4, 8.0)
    assert excursion.ts == T0 + timedelta(seconds=30)
    assert await cb.send(9.6) == []  # still the same excursion
    assert await cb.send(5.0) == []
    (recovered,) = await cb.send(4.6)
    assert (recovered.type, recovered.severity) == ("RECOVERED", "INFO")
    assert (recovered.observed_value, recovered.threshold) == (4.6, 8.0)
    assert [e.type for e in await events_of(session, on_the_road)] == ["EXCURSION", "RECOVERED"]
    assert await shipments.has_open_excursion(session, on_the_road) is True

    # coldchain.* to the shipment's orgs only (CLAUDE.md rule 6): not the other carrier.
    involved = sorted(str(o) for o in (world.hospital_a.id, world.hospital_b.id, swiftmed.org.id))
    (sent,) = await outbox(session, EventType.COLDCHAIN_EXCURSION)
    assert sent["org_ids"] == involved
    assert sent["data"] == {
        "shipment_id": str(on_the_road.id),
        "coldchain_event_id": str(excursion.id),
        "device_id": "cb-01",
        "severity": "ALERT",
        "observed_value": 9.4,
        "threshold": 8.0,
        "ts": excursion.ts.isoformat(),
        "to_org_id": str(world.hospital_a.id),
    }
    (back,) = await outbox(session, EventType.COLDCHAIN_RECOVERED)
    assert (back["org_ids"], back["data"]["observed_value"]) == (involved, 4.6)


async def test_a_single_spike_creates_no_excursion(
    session: AsyncSession, on_the_road: Shipment, client_for: ClientFor, world: World
) -> None:
    cb = Box(session)
    for temps in ((4.0,), (9.5,), (4.2,), (12.0, 4.1), (1.0,), (4.0,)):
        assert await cb.send(*temps) == []
    assert await events_of(session, on_the_road) == []
    for event_type in COLD_EVENTS:
        assert await outbox(session, event_type) == []
    r = await (await client_for(world.users["a.RECEIVER"])).get(f"/shipments/{on_the_road.id}")
    assert r.json()["inspection_note_required"] is False


async def test_the_rules_run_on_linked_readings_only(
    session: AsyncSession, assigned: Shipment, band: Product, box: Device
) -> None:
    """A box on no shipment: its readings are stored but no rule runs on them."""
    assert await Box(session).send(9.1, 9.4, 9.8) == []
    assert await events_of(session, assigned) == []


async def test_a_product_without_a_band_has_no_rule(
    session: AsyncSession, on_the_road: Shipment, band: Product
) -> None:
    band.temp_min_c = band.temp_max_c = None
    await session.flush()
    assert await Box(session).send(25.0, 26.0, 27.0) == []


async def test_audit_rows_are_system_with_the_observed_values_and_threshold(
    session: AsyncSession, world: World, on_the_road: Shipment, swiftmed: Fleet
) -> None:
    cb = Box(session)
    (excursion,) = await cb.send(4.0, 9.1, 9.4)
    (recovered,) = await cb.send(5.0, 4.6)
    rows = await rows_of(session, excursion)
    # The receiving org's trail, mirrored into the source and the carrier.
    assert sorted(str(r.org_id) for r in rows) == sorted(
        str(o) for o in (world.hospital_a.id, world.hospital_b.id, swiftmed.org.id)
    )
    for row in rows:
        assert (row.entity, row.action, row.actor_id, row.reason_source) == (
            "coldchain_event",
            "coldchain_event.excursion",
            None,
            "SYSTEM",
        )
        assert row.reason == (
            "2 consecutive readings from cb-01 outside 2–8 °C: "
            f"9.1 °C at {(T0 + timedelta(seconds=10)):%Y-%m-%d %H:%M:%S} UTC, "
            f"9.4 °C at {(T0 + timedelta(seconds=20)):%Y-%m-%d %H:%M:%S} UTC."
        )
        assert row.after is not None
        assert (row.after["observed_value"], row.after["threshold"], row.after["unit"]) == (
            9.4,
            8.0,
            "°C",
        )
        assert row.after["band"] == {"temp_min_c": 2.0, "temp_max_c": 8.0}
        assert [r["temp_c"] for r in row.after["readings"]] == [9.1, 9.4]
        assert row.after["shipment_id"] == str(on_the_road.id)
    (row, *_) = await rows_of(session, recovered)
    assert (row.action, row.reason_source) == ("coldchain_event.recovered", "SYSTEM")
    assert row.reason.endswith("The excursion stays on record.")
    assert row.after is not None and row.after["observed_value"] == 4.6


async def test_the_shortage_trail_lists_the_cold_chain_events(
    session: AsyncSession, on_the_road: Shipment, client_for: ClientFor, world: World
) -> None:
    await Box(session).send(9.1, 9.4)
    approver = await client_for(world.users["a.APPROVER"])
    r = await approver.get(f"/shortages/{on_the_road.shortage_id}/audit", params={"limit": 200})
    assert r.status_code == 200, r.text
    actions = [row["action"] for row in r.json()["items"]]
    assert "coldchain_event.excursion" in actions


# --- DEVICE_SILENT (worker) --------------------------------------------------------------------


async def test_a_silent_device_on_an_in_transit_shipment_raises_device_silent(
    session: AsyncSession, world: World, on_the_road: Shipment, box: Device, swiftmed: Fleet
) -> None:
    await Box(session).send(4.2)
    await session.refresh(box)
    last = box.last_seen
    assert last == T0
    # Under 2 minutes: nothing.
    assert await coldchain.check_silent_devices(session, now=last + timedelta(seconds=119)) == 0
    at = last + timedelta(seconds=150)
    assert await coldchain.check_silent_devices(session, now=at) == 1
    (silent,) = await events_of(session, on_the_road)
    assert (silent.type, silent.severity, silent.device_id, silent.ts) == (
        "DEVICE_SILENT",
        "WARNING",
        "cb-01",
        at,
    )
    assert (silent.observed_value, silent.threshold) == (150.0, 120.0)
    # Once per silence.
    assert await coldchain.check_silent_devices(session, now=at + timedelta(minutes=5)) == 0
    (sent,) = await outbox(session, EventType.COLDCHAIN_DEVICE_SILENT)
    assert sent["org_ids"] == sorted(
        str(o) for o in (world.hospital_a.id, world.hospital_b.id, swiftmed.org.id)
    )
    assert (sent["data"]["observed_value"], sent["data"]["threshold"]) == (150.0, 120.0)
    rows = await rows_of(session, silent)
    assert {(r.action, r.reason_source, r.actor_id) for r in rows} == {
        ("coldchain_event.device_silent", "SYSTEM", None)
    }
    assert rows[0].reason == (
        f"No reading received from cb-01 since {last:%Y-%m-%d %H:%M:%S} UTC "
        "(150 s; the limit is 120 s)."
    )
    assert rows[0].after is not None and rows[0].after["unit"] == "s"
    # A silence is not an excursion: no inspection note is required for it.
    assert await shipments.has_open_excursion(session, on_the_road) is False


async def test_a_device_that_sends_again_and_falls_silent_again_raises_it_again(
    session: AsyncSession, on_the_road: Shipment, box: Device
) -> None:
    cb = Box(session)
    await cb.send(4.2)
    assert await coldchain.check_silent_devices(session, now=T0 + timedelta(minutes=3)) == 1
    cb.n = 30  # the box comes back 5 minutes after T0
    await cb.send(4.3)
    await session.refresh(box)
    back = box.last_seen
    assert back is not None
    assert await coldchain.check_silent_devices(session, now=back + timedelta(seconds=60)) == 0
    assert await coldchain.check_silent_devices(session, now=back + timedelta(seconds=121)) == 1
    assert [e.type for e in await events_of(session, on_the_road)] == ["DEVICE_SILENT"] * 2


async def test_device_silent_only_while_in_transit(
    session: AsyncSession,
    assigned: Shipment,
    band: Product,
    box: Device,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
) -> None:
    r = await dispatcher.post(f"/devices/{box.id}/assign", json={"shipment_id": str(assigned.id)})
    assert r.status_code == 200
    await Box(session).send(4.2)
    later = T0 + timedelta(minutes=10)
    assert await coldchain.check_silent_devices(session, now=later) == 0  # ASSIGNED
    assert (await step(ravi, assigned, "PICKED_UP")).status_code == 200
    assert await coldchain.check_silent_devices(session, now=later) == 0
    assert (await step(ravi, assigned, "IN_TRANSIT")).status_code == 200
    assert await coldchain.check_silent_devices(session, now=later) == 1
    assert (await step(ravi, assigned, "DELIVERED")).status_code == 200
    assert await coldchain.check_silent_devices(session, now=later + timedelta(hours=1)) == 0


async def test_a_device_that_never_sent_is_silent_from_the_in_transit_time(
    session: AsyncSession, on_the_road: Shipment, box: Device
) -> None:
    assert box.last_seen is None
    (moved,) = [h for h in on_the_road.status_history if h["to"] == "IN_TRANSIT"]
    since = datetime.fromisoformat(moved["at"])
    assert await coldchain.check_silent_devices(session, now=since + timedelta(seconds=100)) == 0
    assert await coldchain.check_silent_devices(session, now=since + timedelta(seconds=125)) == 1
    (silent,) = await events_of(session, on_the_road)
    assert silent.observed_value == 125.0


async def test_a_shipment_without_a_device_is_never_silent(
    session: AsyncSession, assigned: Shipment, ravi: httpx.AsyncClient
) -> None:
    for status in ("PICKED_UP", "IN_TRANSIT"):
        assert (await step(ravi, assigned, status)).status_code == 200
    assert await coldchain.check_silent_devices(session, now=T0 + timedelta(hours=2)) == 0


# --- GET /shipments/{id}/coldchain --------------------------------------------------------------


async def test_the_involved_orgs_read_the_readings_and_events(
    session: AsyncSession,
    world: World,
    on_the_road: Shipment,
    client_for: ClientFor,
    dispatcher: httpx.AsyncClient,
) -> None:
    await Box(session).send(4.1, 9.1, 9.4)
    for client in (
        dispatcher,
        await client_for(world.users["a.RECEIVER"]),
        await client_for(world.users["b.STORE_MANAGER"]),
    ):
        r = await client.get(f"/shipments/{on_the_road.id}/coldchain")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["band"] == {"temp_min_c": 2.0, "temp_max_c": 8.0}
        assert [x["temp_c"] for x in body["readings"]] == [4.1, 9.1, 9.4]
        assert body["device"]["device_id"] == "cb-01"
        assert body["device"]["battery_level"] == 80
        assert body["silent_after_seconds"] == 120
        (event,) = body["events"]
        assert (event["type"], event["observed_value"], event["threshold"]) == (
            "EXCURSION",
            9.4,
            8.0,
        )
        assert body["has_excursion"] is True
    # The newest readings, oldest first.
    r = await dispatcher.get(f"/shipments/{on_the_road.id}/coldchain", params={"limit": 2})
    assert [x["temp_c"] for x in r.json()["readings"]] == [9.1, 9.4]


async def test_other_orgs_get_403_and_an_unknown_shipment_404(
    session: AsyncSession,
    world: World,
    on_the_road: Shipment,
    client_for: ClientFor,
    other_dispatcher: httpx.AsyncClient,
) -> None:
    for client in (other_dispatcher, await client_for(world.users["s.SUPPLIER_DESK"])):
        r = await client.get(f"/shipments/{on_the_road.id}/coldchain")
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    r = await other_dispatcher.get(f"/shipments/{uuid.uuid4()}/coldchain")
    assert r.status_code == 404


async def test_an_unassigned_shipment_on_the_board_is_not_another_carriers_business(
    shipment: Shipment, other_dispatcher: httpx.AsyncClient
) -> None:
    """Every logistics org sees a CREATED shipment on the dispatch board, but only the
    shipment's own orgs read its cold chain."""
    assert (await other_dispatcher.get(f"/shipments/{shipment.id}")).status_code == 200
    r = await other_dispatcher.get(f"/shipments/{shipment.id}/coldchain")
    assert r.status_code == 403


async def test_a_carrier_sees_only_its_own_devices_data(
    session: AsyncSession,
    world: World,
    on_the_road: Shipment,
    other_fleet: Fleet,
    client_for: ClientFor,
    dispatcher: httpx.AsyncClient,
) -> None:
    """Readings and events of another org's box on the same shipment (e.g. an earlier
    carrier's) reach the sender and receiver, not this carrier."""
    theirs = Device(org_id=other_fleet.org.id, device_id="cb-77")
    session.add(theirs)
    await Box(session).send(4.0)
    # Simulate the other org's box having ridden with this shipment.
    box = await session.scalar(select(Device).where(Device.device_id == "cb-01"))
    assert box is not None
    box.assigned_shipment_id, theirs.assigned_shipment_id = None, on_the_road.id
    on_the_road.device_id = theirs.id
    await session.flush()
    later = Box(session, "cb-77")
    later.n = 1  # after cb-01's reading
    assert await later.send(9.1, 9.4) != []

    mine = (await dispatcher.get(f"/shipments/{on_the_road.id}/coldchain")).json()
    assert [x["device_id"] for x in mine["readings"]] == ["cb-01"]
    assert (mine["events"], mine["device"], mine["has_excursion"]) == ([], None, False)
    receiver = await client_for(world.users["a.RECEIVER"])
    theirs_view = (await receiver.get(f"/shipments/{on_the_road.id}/coldchain")).json()
    assert [x["device_id"] for x in theirs_view["readings"]] == ["cb-01", "cb-77", "cb-77"]
    assert theirs_view["device"]["device_id"] == "cb-77"
    # The list badge follows the same rule.
    page = (await dispatcher.get("/shipments", params={"status": "IN_TRANSIT"})).json()
    assert page["items"][0]["coldchain"] is None
    inbound = (await receiver.get("/shipments", params={"direction": "inbound"})).json()
    assert inbound["items"][0]["coldchain"]["last_event_type"] == "EXCURSION"


async def test_shipment_lists_carry_the_cold_chain_summary(
    session: AsyncSession, on_the_road: Shipment, dispatcher: httpx.AsyncClient
) -> None:
    page = (await dispatcher.get("/shipments", params={"status": "IN_TRANSIT"})).json()
    assert page["items"][0]["coldchain"] is None
    cb = Box(session)
    await cb.send(9.1, 9.4)
    item = (await dispatcher.get("/shipments", params={"status": "IN_TRANSIT"})).json()["items"][0]
    assert item["coldchain"]["last_event_type"] == "EXCURSION"
    assert item["coldchain"]["had_excursion"] is True
    await cb.send(4.0, 4.1)
    detail = (await dispatcher.get(f"/shipments/{on_the_road.id}")).json()
    assert detail["coldchain"]["last_event_type"] == "RECOVERED"
    assert detail["coldchain"]["had_excursion"] is True


# --- the receipt needs an inspection note once an excursion is on record (§9) ------------------


async def test_accepting_after_an_excursion_needs_an_inspection_note(
    session: AsyncSession,
    world: World,
    on_the_road: Shipment,
    ravi: httpx.AsyncClient,
    client_for: ClientFor,
) -> None:
    cb = Box(session)
    await cb.send(4.0, 9.1, 9.4)
    await cb.send(5.0, 4.6)  # recovered: the excursion stays on record
    assert (await step(ravi, on_the_road, "DELIVERED")).status_code == 200
    receiver = await client_for(world.users["a.RECEIVER"])
    url = f"/shipments/{on_the_road.id}"
    assert (await receiver.get(url)).json()["inspection_note_required"] is True

    for note in (None, "  "):
        body = receipt(850, 850, inspection_note=note)
        r = await receiver.post(f"{url}/receipt", json=body)
        assert (r.status_code, r.json()["details"]) == (400, {"reason": "inspection_note_required"})

    note = "Probe read 4.1 °C on arrival; kits sealed, indicator strips unchanged."
    r = await receiver.post(f"{url}/receipt", json=receipt(850, 850, inspection_note=note))
    assert r.status_code == 201, r.text
    assert r.json()["inspection_note"] == note
    (row,) = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(r.json()["id"]),
                AuditLog.action == "receipt.recorded",
            )
        )
    ).all()
    assert row.after is not None and row.after["inspection_note"] == note
    assert row.org_id == world.hospital_a.id
