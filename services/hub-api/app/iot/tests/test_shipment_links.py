"""S14 part 3: POST /devices/{id}/assign, and readings linked to the device's shipment (and
sent as `coldchain.reading`) only while that shipment is ASSIGNED, PICKED_UP or IN_TRANSIT."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.iot import service as iot
from app.iot.models import Device, SensorReading
from app.iot.schemas import ReadingIn, TelemetryBatch
from app.recommendations.tests.conftest import audit_of, outbox
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


@pytest.fixture
async def box(session: AsyncSession, swiftmed: Fleet) -> Device:
    device = Device(org_id=swiftmed.org.id, device_id="cb-01")
    session.add(device)
    await session.flush()
    return device


async def put_on(client: httpx.AsyncClient, device: Device, shipment_id: object) -> httpx.Response:
    body = {"shipment_id": str(shipment_id) if shipment_id else None}
    return await client.post(f"/devices/{device.id}/assign", json=body)


async def send(session: AsyncSession, n: int, temp: float = 4.2) -> list[SensorReading]:
    """`n` new readings from cb-01, a second apart after the last batch."""
    start = T0 + timedelta(seconds=len(list(await session.scalars(select(SensorReading)))))
    batch = TelemetryBatch(
        readings=[
            ReadingIn(device_id="cb-01", ts=start + timedelta(seconds=i), temp_c=temp)
            for i in range(n)
        ]
    )
    assert (await iot.ingest(session, batch)).stored == n
    stmt = select(SensorReading).where(SensorReading.ts >= start).order_by(SensorReading.ts)
    return list(await session.scalars(stmt))


async def test_the_dispatcher_puts_a_box_on_a_shipment(
    session: AsyncSession,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    box: Device,
    swiftmed: Fleet,
) -> None:
    r = await put_on(dispatcher, box, shipment.id)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_shipment_id"] == str(shipment.id)
    await session.refresh(shipment)
    assert shipment.device_id == box.id
    (row,) = await audit_of(session, box.id)
    assert (row.action, row.org_id, row.actor_id) == (
        "device.assigned",
        swiftmed.org.id,
        swiftmed.dispatcher.id,
    )
    assert (row.before, row.after) == (
        {"assigned_shipment_id": None},
        {"assigned_shipment_id": str(shipment.id)},
    )
    r = await dispatcher.get("/devices")
    assert r.json()["items"][0]["assigned_shipment_id"] == str(shipment.id)
    # Taking it off.
    r = await put_on(dispatcher, box, None)
    assert r.status_code == 200
    assert r.json()["assigned_shipment_id"] is None
    device_on = await session.scalar(
        select(Shipment.device_id)
        .where(Shipment.id == shipment.id)
        .execution_options(populate_existing=True)
    )
    assert device_on is None
    assert (await audit_of(session, box.id))[-1].action == "device.unassigned"


async def test_another_orgs_device_or_shipment_is_403(
    world: World,
    assigned: Shipment,
    other_dispatcher: httpx.AsyncClient,
    dispatcher: httpx.AsyncClient,
    box: Device,
    client_for: ClientFor,
) -> None:
    # Another logistics org cannot move SwiftMed's box.
    assert (await put_on(other_dispatcher, box, assigned.id)).status_code == 403
    # Nor can a hospital user without shipment.assign.
    assert (
        await put_on(await client_for(world.users["a.REQUESTER"]), box, assigned.id)
    ).status_code == 403
    assert (
        await dispatcher.post(f"/devices/{uuid.uuid4()}/assign", json={"shipment_id": None})
    ).status_code == 404


async def test_a_box_cannot_go_on_a_shipment_its_org_cannot_see(
    session: AsyncSession,
    assigned: Shipment,
    other_fleet: Fleet,
    client_for: ClientFor,
) -> None:
    theirs = Device(org_id=other_fleet.org.id, device_id="cb-77")
    session.add(theirs)
    await session.flush()
    # SwiftMed carries `assigned`; the other logistics org no longer sees it.
    r = await put_on(await client_for(other_fleet.dispatcher), theirs, assigned.id)
    assert r.status_code == 403


async def test_a_delivered_shipment_or_one_with_another_box_is_409(
    session: AsyncSession,
    assigned: Shipment,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    box: Device,
    swiftmed: Fleet,
) -> None:
    second = Device(org_id=swiftmed.org.id, device_id="cb-02")
    session.add(second)
    await session.flush()
    assert (await put_on(dispatcher, box, assigned.id)).status_code == 200
    r = await put_on(dispatcher, second, assigned.id)
    assert r.status_code == 409
    assert r.json()["details"] == {"device_id": str(box.id)}
    for status in ("PICKED_UP", "IN_TRANSIT", "DELIVERED"):
        assert (await step(ravi, assigned, status)).status_code == 200
    assert (await put_on(dispatcher, box, None)).status_code == 200
    r = await put_on(dispatcher, box, assigned.id)
    assert r.status_code == 409
    assert r.json()["details"] == {"status": "DELIVERED"}


async def test_readings_are_linked_and_sent_only_while_the_shipment_is_on_its_way(
    session: AsyncSession,
    world: World,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    ravi: httpx.AsyncClient,
    box: Device,
    swiftmed: Fleet,
) -> None:
    # A box on no shipment: readings are stored, unlinked, and nothing is sent.
    assert [r.shipment_id for r in await send(session, 2)] == [None, None]
    # On a CREATED shipment: still unlinked.
    assert (await put_on(dispatcher, box, shipment.id)).status_code == 200
    assert [r.shipment_id for r in await send(session, 1)] == [None]
    assert await outbox(session, EventType.COLDCHAIN_READING) == []

    assert (await s11.assign(dispatcher, shipment, swiftmed)).status_code == 200
    linked = await send(session, 2, temp=4.5)  # ASSIGNED
    assert [r.shipment_id for r in linked] == [shipment.id, shipment.id]
    assert (await step(ravi, shipment, "PICKED_UP")).status_code == 200
    assert [r.shipment_id for r in await send(session, 1)] == [shipment.id]
    assert (await step(ravi, shipment, "IN_TRANSIT")).status_code == 200
    assert [r.shipment_id for r in await send(session, 1, temp=9.1)] == [shipment.id]
    assert (await step(ravi, shipment, "DELIVERED")).status_code == 200
    assert [r.shipment_id for r in await send(session, 1)] == [None]

    events = await outbox(session, EventType.COLDCHAIN_READING)
    assert len(events) == 4
    assert all(
        e["org_ids"]
        == sorted(str(o) for o in (world.hospital_a.id, world.hospital_b.id, swiftmed.org.id))
        for e in events
    )
    assert events[0]["data"] == {
        "shipment_id": str(shipment.id),
        "temp_c": 4.5,
        "ts": linked[0].ts.isoformat(),
    }
    assert events[-1]["data"]["temp_c"] == 9.1


async def test_a_replayed_reading_is_not_sent_again(
    session: AsyncSession, assigned: Shipment, dispatcher: httpx.AsyncClient, box: Device
) -> None:
    assert (await put_on(dispatcher, box, assigned.id)).status_code == 200
    batch = TelemetryBatch(readings=[ReadingIn(device_id="cb-01", ts=T0, temp_c=4.0)])
    assert (await iot.ingest(session, batch)).stored == 1
    assert (await iot.ingest(session, batch)).duplicates == 1
    assert len(await outbox(session, EventType.COLDCHAIN_READING)) == 1
