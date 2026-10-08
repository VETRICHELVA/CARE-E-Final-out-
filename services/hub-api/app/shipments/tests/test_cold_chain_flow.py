"""A cold-chain product end to end (Scenario 2's shape): the cold_chain gate passes once a
cold-chain vehicle exists in the network (business-rules.md §3, read literally), the
approved transfer is a cold-chain shipment, and only a cold-chain vehicle may carry it."""

from datetime import datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.recommendations.tests.conftest import open_rec
from app.shipments.models import Shipment
from app.shipments.tests.conftest import Fleet, add_fleet
from app.shortages import service as shortages
from app.shortages.models import Priority
from app.source_requests.tests.conftest import (
    add_batch,
    authorize,
    create_shortage,
    requests_of,
)

pytestmark = pytest.mark.anyio


async def test_with_a_cold_chain_vehicle_in_the_network_the_gate_passes_and_only_it_can_carry(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    now: datetime,
    client_for: ClientFor,
) -> None:
    rdk = products["DIAG-RDK"]  # 2-8 C
    assert not await shortages.cold_chain_vehicle_exists(session)
    # The vehicle belongs to a logistics org that is not involved in the shortage at all.
    fleet: Fleet = await add_fleet(session, "Cold Logistics", "cold.test", ["Ravi"], cold=True)
    assert await shortages.cold_chain_vehicle_exists(session)
    await add_batch(session, world.hospital_b, rdk, now, on_hand=500, expiry_days=240)
    authorize(session, rdk, world.hospital_b)
    await session.flush()
    shortage = await create_shortage(
        session,
        world.users["a.REQUESTER"],
        rdk,
        now,
        priority=Priority.ROUTINE,
        qty_required=200,
        qty_local_usable=0,
    )
    (sr,) = await requests_of(session, shortage)  # TRANSFER from B: every gate passed
    manager_b = await client_for(world.users["b.STORE_MANAGER"])
    assert (await manager_b.post(f"/source-requests/{sr.id}/accept", json={})).status_code == 200
    rec = await open_rec(session, shortage)
    r = await (await client_for(world.users["a.APPROVER"])).post(
        f"/recommendations/{rec.id}/approve", json={}
    )
    assert r.status_code == 200, r.text
    shipment = await session.scalar(select(Shipment).where(Shipment.source_request_id == sr.id))
    assert shipment is not None and shipment.requires_cold_chain

    dispatcher: httpx.AsyncClient = await client_for(fleet.dispatcher)
    driver = str(fleet.drivers["Ravi"].id)
    r = await dispatcher.post(
        f"/shipments/{shipment.id}/assign",
        json={"driver_id": driver, "vehicle_id": str(fleet.van.id)},
    )
    assert r.status_code == 400
    assert "needs a cold-chain vehicle" in r.json()["message"]
    assert fleet.cold_van is not None
    r = await dispatcher.post(
        f"/shipments/{shipment.id}/assign",
        json={"driver_id": driver, "vehicle_id": str(fleet.cold_van.id)},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["requires_cold_chain"]) == ("ASSIGNED", True)


async def test_a_plain_vehicle_does_not_count_for_the_gate(
    session: AsyncSession, world: World
) -> None:
    await add_fleet(session, "Plain Logistics", "plain.test", ["Omar"], cold=False)
    assert not await shortages.cold_chain_vehicle_exists(session)
