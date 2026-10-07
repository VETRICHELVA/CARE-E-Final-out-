"""S11 fixtures: Scenario 1 (S06/S09 fixtures) up to an approved TRANSFER from Hospital B,
SwiftMed Logistics with drivers Ravi and Priya and two vans (one cold-chain), and a second
logistics org with its own dispatcher, driver and van."""

from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.conftest import ClientFor, World
from app.orgs.models import Organization, OrgType
from app.recommendations.tests import conftest as s09
from app.recommendations.tests.conftest import answer_b, open_rec
from app.shipments.models import Driver, Shipment, Vehicle
from app.shortages.models import Shortage
from app.source_requests.tests.conftest import add_org, add_user

now = s09.now
s1 = s09.s1
shortage = s09.shortage


@dataclass
class Fleet:
    org: Organization
    dispatcher: User
    drivers: dict[str, Driver]  # by first name
    users: dict[str, User]  # the drivers' users, by first name
    van: Vehicle  # no cold chain
    cold_van: Vehicle | None


async def add_fleet(
    session: AsyncSession, name: str, domain: str, drivers: list[str], *, cold: bool
) -> Fleet:
    org = await add_org(session, name, OrgType.LOGISTICS, 12.98, 77.64)
    dispatcher = await add_user(session, org, "DISPATCHER", f"dispatcher@{domain}")
    users, rows = {}, {}
    for n, first in enumerate(drivers):
        user = await add_user(session, org, "DRIVER", f"{first.lower()}@{domain}")
        user.full_name = first
        rows[first] = Driver(org_id=org.id, user_id=user.id, phone=f"+91 90000 0000{n}")
        users[first] = user
    van = Vehicle(org_id=org.id, reg_no=f"{domain[:2].upper()}-VAN-1", has_cold_chain=False)
    cold_van = (
        Vehicle(org_id=org.id, reg_no=f"{domain[:2].upper()}-COLD-1", has_cold_chain=True)
        if cold
        else None
    )
    session.add_all([*rows.values(), van, *([cold_van] if cold_van else [])])
    await session.flush()
    return Fleet(org, dispatcher, rows, users, van, cold_van)


@pytest.fixture
async def swiftmed(session: AsyncSession) -> Fleet:
    return await add_fleet(
        session, "SwiftMed Logistics", "swiftmed.test", ["Ravi", "Priya"], cold=True
    )


@pytest.fixture
async def other_fleet(session: AsyncSession) -> Fleet:
    return await add_fleet(session, "Other Logistics", "other.test", ["Omar"], cold=False)


async def approve_transfer(
    session: AsyncSession, client_for: ClientFor, world: World, shortage: Shortage
) -> Shipment:
    """Scenario 1 with B accepting: the TRANSFER of 850 from B is approved (holds FIRM)."""
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "accept")
    rec = await open_rec(session, shortage)
    r = await (await client_for(world.users["a.APPROVER"])).post(
        f"/recommendations/{rec.id}/approve", json={}
    )
    assert r.status_code == 200, r.text
    (shipment_id,) = r.json()["shipment_ids"]
    return await session.get_one(Shipment, shipment_id)


@pytest.fixture
async def shipment(
    session: AsyncSession,
    world: World,
    shortage: Shortage,
    client_for: ClientFor,
    swiftmed: Fleet,
    other_fleet: Fleet,
) -> Shipment:
    """CREATED: B -> A, 850 Surgical Kit A, waiting for a carrier."""
    return await approve_transfer(session, client_for, world, shortage)


@pytest.fixture
async def dispatcher(client_for: ClientFor, swiftmed: Fleet) -> httpx.AsyncClient:
    return await client_for(swiftmed.dispatcher)


@pytest.fixture
async def ravi(client_for: ClientFor, swiftmed: Fleet) -> httpx.AsyncClient:
    return await client_for(swiftmed.users["Ravi"])


@pytest.fixture
async def priya(client_for: ClientFor, swiftmed: Fleet) -> httpx.AsyncClient:
    return await client_for(swiftmed.users["Priya"])


@pytest.fixture
async def omar(client_for: ClientFor, other_fleet: Fleet) -> httpx.AsyncClient:
    return await client_for(other_fleet.users["Omar"])


@pytest.fixture
async def other_dispatcher(client_for: ClientFor, other_fleet: Fleet) -> httpx.AsyncClient:
    return await client_for(other_fleet.dispatcher)


async def assign(
    dispatcher: httpx.AsyncClient, shipment: Shipment, fleet: Fleet, *, cold: bool = False
) -> httpx.Response:
    vehicle = fleet.cold_van if cold else fleet.van
    assert vehicle is not None
    return await dispatcher.post(
        f"/shipments/{shipment.id}/assign",
        json={"driver_id": str(fleet.drivers["Ravi"].id), "vehicle_id": str(vehicle.id)},
    )


@pytest.fixture
async def assigned(
    session: AsyncSession, shipment: Shipment, dispatcher: httpx.AsyncClient, swiftmed: Fleet
) -> Shipment:
    """ASSIGNED to Ravi in SwiftMed's plain van."""
    r = await assign(dispatcher, shipment, swiftmed)
    assert r.status_code == 200, r.text
    await session.refresh(shipment)
    return shipment


async def step(driver: httpx.AsyncClient, shipment: Shipment, status: str) -> httpx.Response:
    return await driver.post(f"/shipments/{shipment.id}/status", json={"status": status})
